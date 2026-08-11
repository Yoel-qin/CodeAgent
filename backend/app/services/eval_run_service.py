"""检索评测运行编排 + 持久化（Phase 9 评测产品化 M27 / A/B 消融产品化 M28 / 收尾 M29）。

复用 ``app.eval.eval_service.run_eval``（走真实全漏斗）跑一轮评测，把结果落 ``EvalRun`` 行，
供 ``POST /v1/eval/run`` 与前端 EvalPage 历史/趋势。同步执行（对齐 ``sync/trigger``：跑完返
COMPLETED/FAILED），``run_eval`` 本身全 async 故无需 ``asyncio.to_thread``。

- ``run_and_persist``：load 评测集 → 跑 → 填 aggregate/per_query/unresolved → 提交；异常翻 FAILED。
  ``persist=False`` 不写库，仍返内存合成的完整 ``EvalRun``（「预览不污染历史」）。
  ``ablation``（M29）：可选 ``AblationConfig`` 子集（如 ``{"rerank": False}``），注入 ``recall_fn``
  跑单变体——让单次评测也能探单一环节，不必另开 A/B 跑全套 pair。落 ``config["ablation"]``。
- ``run_ab_and_persist``（M28）：镜像上者，但跑 ``run_ab``（A/B 消融）并落 ``config.kind="ab"``
  区分行；完整 ABReport 存 ``config["report"]``，``aggregate`` 列冗余一份 ``full`` 变体供趋势图。
- ``list_runs`` / ``get_run``：历史列表（desc by created_at，可选 ``kind`` 过滤）/ 单条详情。
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.eval import EvalRun
from app.eval.ab_service import DEFAULT_PAIRS, ABPair, _make_recall_fn, filter_by_tag, run_ab
from app.eval.eval_service import load_eval_queries, run_eval
from app.retrieval.ablation import AblationConfig

# app/services/eval_run_service.py → parents[2] = backend/；与 CLI 的 _BACKEND_ROOT 算法一致
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVAL_SET = _BACKEND_ROOT / "eval" / "eval_set.yaml"

# pair 名称 → ABPair（镜像 scripts/ab_eval.py:45）；API/service 按 pair 名称解析
_PAIR_BY_NAME: dict[str, ABPair] = {p.name: p for p in DEFAULT_PAIRS}

# A/B 变体 per_query 里属于「重负载诊断」的字段（M25 向量路诊断），diagnose=False 时裁剪
_DIAGNOSE_FIELDS = ("recall_paths", "retrieved_kinds")


def _normalize_agg(agg: dict | None) -> dict | None:
    """``metrics.aggregate`` 的 recall/precision/ndcg 是 int key（``{1:..,10:..}``），
    JSONB/JSON 序列化后会变字符串 key。这里统一规整为字符串 key，使 persist 与非 persist
    路径返回的 aggregate 形状一致（前端按 ``["10"]`` 索引）。
    """
    if not agg:
        return None
    return {
        "n": agg.get("n"),
        "recall": {str(k): v for k, v in (agg.get("recall") or {}).items()},
        "precision": {str(k): v for k, v in (agg.get("precision") or {}).items()},
        "mrr": agg.get("mrr"),
        "ndcg": {str(k): v for k, v in (agg.get("ndcg") or {}).items()},
    }


def _build_ablation_recall_fn(ablation: dict[str, bool]):
    """``{"rerank": False, ...}`` → 固定消融配置的 recall 包装器（注入 ``run_eval`` 的 ``recall_fn``）。

    复用 ``ab_service._make_recall_fn``（同一注入机制，A/B 跑变体时即用它）。未知字段/非 bool
    值经 ``AblationConfig(**ablation)`` 校验失败 → 抛 ValueError（API 层转 422，对齐 A/B 的
    ``_resolve_pairs``）。
    """
    try:
        cfg = AblationConfig(**ablation)
    except TypeError as e:  # 未知字段
        raise ValueError(f"未知 ablation 字段: {ablation}（可选 vector/lexical/graph/rerank）") from e
    return _make_recall_fn(cfg)


async def run_and_persist(
    session: AsyncSession,
    *,
    top_k: int = 10,
    rewrite: str = "off",
    eval_set: str | None = None,
    ablation: dict[str, bool] | None = None,
    trigger: str = "api",
    persist: bool = True,
) -> EvalRun:
    """跑一轮评测并（默认）持久化。返回 ``EvalRun``（含完整 aggregate/per_query）。

    ``ablation``（M29）：可选 ``AblationConfig`` 子集（如 ``{"rerank": False}``），经 ``recall_fn``
    注入跑单变体——让单次评测也能探单一环节，落 ``config["ablation"]``。``None`` = 全开 = 生产
    （逐字同 M27 行为）。
    """
    path = eval_set or str(DEFAULT_EVAL_SET)
    queries = load_eval_queries(path)
    started = datetime.now(UTC)

    run = EvalRun(
        status="PENDING",
        trigger=trigger,
        top_k=top_k,
        rewrite=rewrite,
        embedding_strategy=settings.embedding_strategy,
        n_queries=len(queries),
        started_at=started,
        config={
            "top_k": top_k,
            "rewrite": rewrite,
            "eval_set": path,
            "embedding_strategy": settings.embedding_strategy,
            **({"ablation": ablation} if ablation else {}),
        },
    )
    if persist:
        session.add(run)
        await session.flush()  # 取 run_id

    try:
        # ablation 非 None → 注入固定消融 recall_fn（跑单变体）；None → 默认生产链路（逐字同 M27）
        recall_fn = _build_ablation_recall_fn(ablation) if ablation else None
        report = await run_eval(session, queries, top_k=top_k, rewrite=rewrite, recall_fn=recall_fn)
        run.status = "COMPLETED"
        run.aggregate = _normalize_agg(report.aggregate)
        run.n_evaluable = report.n_evaluable
        run.rerank_on_count = report.rerank_on_count
        run.per_query = report.per_query
        run.unresolved = report.unresolved
        run.config = {**(run.config or {}), **report.config}
    except Exception as e:  # 整轮失败仍落 FAILED 行（含 error_message），对齐 SyncTask 语义
        run.status = "FAILED"
        run.error_message = f"{type(e).__name__}: {e}"
    finally:
        run.completed_at = datetime.now(UTC)
        run.duration_ms = int((run.completed_at - started).total_seconds() * 1000)
        if persist:
            await session.commit()
    return run


async def list_runs(
    session: AsyncSession, *, limit: int = 50, kind: str | None = None
) -> list[EvalRun]:
    """历史列表（最新在前）。

    ``kind`` 为 ``None`` 返回全部（前端统一历史表）；``"ab"`` / ``"single"`` 按
    ``config.kind`` 过滤（单次 run 无此键视作 ``"single"``）。
    """
    stmt = select(EvalRun)
    if kind:
        # config.kind 为 JSONB 字符串；单次 run 无该键 → coalesce 成 "single" 再比
        k = func.coalesce(EvalRun.config["kind"].as_string(), "single")
        stmt = stmt.where(k == kind)
    stmt = stmt.order_by(EvalRun.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_run(session: AsyncSession, run_id: int) -> EvalRun | None:
    """单条详情（含 per_query）。"""
    return await session.get(EvalRun, run_id)


def _resolve_pairs(names: Sequence[str] | None) -> list[ABPair]:
    """pair 名称列表 → ``ABPair``；未知名称抛 ValueError（API 层转 422）。None → 默认 3 组。"""
    if not names:
        return list(DEFAULT_PAIRS)
    unknown = [n for n in names if n not in _PAIR_BY_NAME]
    if unknown:
        raise ValueError(f"未知 A/B pair: {unknown}（可选 {sorted(_PAIR_BY_NAME)}）")
    return [_PAIR_BY_NAME[n] for n in names]


def _trim_ab_report(report_dict: dict, *, diagnose: bool) -> dict:
    """按 ``diagnose`` 裁剪 ABReport.to_dict() 的变体 per_query 重负载字段。

    diagnose=False：去掉每变体 per_query 里的 ``recall_paths``/``retrieved_kinds``（M25 诊断
    字段，~85q×4变体×3子链 偏重），保留 metrics；diagnose=True：原样返回。
    """
    if diagnose:
        return report_dict
    variants = report_dict.get("variants") or {}
    for v in variants.values():
        for q in v.get("per_query") or []:
            for f in _DIAGNOSE_FIELDS:
                q.pop(f, None)
    return report_dict


async def run_ab_and_persist(
    session: AsyncSession,
    *,
    top_k: int = 10,
    rewrite: str = "off",
    eval_set: str | None = None,
    pairs: Sequence[str] | None = None,
    graph_subset: bool = False,
    diagnose: bool = False,
    trigger: str = "api",
    persist: bool = True,
) -> EvalRun:
    """跑一轮 A/B 消融并（默认）持久化。镜像 :func:`run_and_persist` 的时序与异常语义。

    - 复用 :func:`app.eval.ab_service.run_ab`（经 ``AblationConfig`` 注入跑真实全漏斗变体）。
    - 落 ``EvalRun`` 行：``config.kind="ab"`` 区分单次评测；完整 ABReport 存 ``config["report"]``；
      ``aggregate`` 列冗余 ``full`` 变体的规整 aggregate，让趋势图把 A/B 行画作「full(生产)」锚点。
    - ``graph_subset=True``：对 ``call_chain`` 标签子集额外跑 ``graph`` pair（镜像 CLI ``--graph-subset``）。
    - ``diagnose=False``：裁剪变体 per_query 的诊断重字段（``recall_paths``/``retrieved_kinds``）。
    """
    path = eval_set or str(DEFAULT_EVAL_SET)
    queries = load_eval_queries(path)
    selected = _resolve_pairs(pairs)
    started = datetime.now(UTC)

    run = EvalRun(
        status="PENDING",
        trigger=trigger,
        top_k=top_k,
        rewrite=rewrite,
        embedding_strategy=settings.embedding_strategy,
        n_queries=len(queries),
        started_at=started,
        config={
            "kind": "ab",
            "top_k": top_k,
            "rewrite": rewrite,
            "eval_set": path,
            "embedding_strategy": settings.embedding_strategy,
            "pairs": [p.name for p in selected],
            "graph_subset": graph_subset,
        },
    )
    if persist:
        session.add(run)
        await session.flush()  # 取 run_id

    try:
        report = await run_ab(session, queries, top_k=top_k, rewrite=rewrite, pairs=selected)
        full = report.variants.get("full", {})
        run.status = "COMPLETED"
        run.aggregate = _normalize_agg(full.get("aggregate"))
        run.n_evaluable = full.get("n_evaluable", 0)
        run.rerank_on_count = full.get("rerank_on_count", 0)
        report_dict = _trim_ab_report(report.to_dict(), diagnose=diagnose)
        # graph_subset 子集报告（若启用且 graph pair 在选集内）
        if graph_subset and any(p.name == "graph" for p in selected):
            cc = filter_by_tag(queries, "call_chain")
            if cc:
                sub = await run_ab(
                    session, cc, top_k=top_k, rewrite=rewrite,
                    pairs=[p for p in selected if p.name == "graph"],
                )
                report_dict["graph_subset"] = _trim_ab_report(sub.to_dict(), diagnose=diagnose)
        run.config = {**(run.config or {}), "report": report_dict}
    except Exception as e:  # 整轮失败仍落 FAILED 行（含 error_message），对齐 run_and_persist
        run.status = "FAILED"
        run.error_message = f"{type(e).__name__}: {e}"
    finally:
        run.completed_at = datetime.now(UTC)
        run.duration_ms = int((run.completed_at - started).total_seconds() * 1000)
        if persist:
            await session.commit()
    return run
