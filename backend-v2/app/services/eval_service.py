"""评测编排服务（M8）：golden 加载 → 锚点解析（PG）→ 逐变体跑 harness → 匹配聚合
→ judge → eval_runs 落账（RUNNING→DONE/FAILED）。CLI 与 REST 共用同一条路径。

session 策略：锚点解析/落账各自短会话（``SessionLocal``），funnel 本身不持 DB 会话
（harness 零 DB）；``persist=False`` 供 CLI 预览（不建行、不落账）。

brief 适配（有据偏差，须带入评审）：
1. ``resolve_anchors`` 的 ``session.execute`` 经 ``_execute`` 兼容层——brief 逐字测试把
   conftest 的**同步** ``session`` fixture 传入本 async 服务，SQLAlchemy 同步
   ``Session.execute`` 返回 Result 不可 await（实测 TypeError）；按「结果是否 awaitable」
   分流，同步/异步会话同一结果形状（生产 ``SessionLocal`` 的 AsyncSession 路不变；
   session 注解随之声明 ``AsyncSession | Session``）。
2. ``judge_case``/``judge_scores`` 直接入本模块命名空间并直调——brief 逐字测试
   ``monkeypatch.setattr(eval_service, "judge_case", ...)`` 要求该名字存在且为实际
   调用点（brief 实现经 ``judge_mod.judge_case`` 调用则钉不住）；run_case 同理本就直入。
"""
from __future__ import annotations

import inspect
from dataclasses import asdict, replace
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import CodeEntity, DocSection, Document, EvalRun
from app.eval import golden, metrics
from app.eval.harness import CaseEvidence, EvalVariant, build_row, run_case
from app.eval.judge import judge_case, judge_scores

__all__ = ["fix_repos", "get_run", "list_runs", "resolve_anchors", "run_and_persist"]


async def _execute(session: AsyncSession | Session, stmt):
    """兼容执行：AsyncSession.execute 是协程 → await；同步 Session（测试 fixture）直调。"""
    result = session.execute(stmt)
    if inspect.isawaitable(result):
        result = await result
    return result


def fix_repos(cases: list[golden.GoldenCase], run_repo: str) -> list[golden.GoldenCase]:
    """空 repo 的 case 填 run_repo（case 级 repo 优先，保持不变）。"""
    return [replace(c, repo=c.repo or run_repo) for c in cases]


async def resolve_anchors(session: AsyncSession | Session,
                          cases: list[golden.GoldenCase]) -> dict[str, dict]:
    """逐 case 解析锚点 → 目标集（code 按 repo+class 批量查；doc 按 repo+doc_name 批量查）。

    未解析的 spec 目标集为空列表（不抛——run 时进 unresolved，validate 时报告）。
    """
    code_rows: dict[str, list[dict]] = {}
    for repo in {c.repo for c in cases if c.expect_code}:
        classes = {golden.parse_code_spec(s)[0]
                   for c in cases if c.repo == repo for s in c.expect_code}
        rows = (await _execute(session,
            select(CodeEntity.class_name, CodeEntity.method_name, CodeEntity.file_path,
                   CodeEntity.start_line, CodeEntity.end_line)
            .where(CodeEntity.repo == repo, CodeEntity.class_name.in_(classes)))).mappings().all()
        code_rows[repo] = [dict(r) for r in rows]
    doc_rows: dict[str, list[dict]] = {}
    for repo in {c.repo for c in cases if c.expect_doc}:
        names = {s.split("#", 1)[0] for c in cases if c.repo == repo for s in c.expect_doc}
        rows = (await _execute(session,
            select(Document.doc_name, DocSection.anchor)
            .select_from(DocSection)
            .join(Document, DocSection.document_id == Document.id)
            .where(Document.repo == repo, Document.doc_name.in_(names)))).mappings().all()
        doc_rows[repo] = [dict(r) for r in rows]
    out: dict[str, dict] = {}
    for c in cases:
        out[c.id] = {
            "code": {s: golden.resolve_code_targets(code_rows.get(c.repo, []), s)
                     for s in c.expect_code},
            "doc": {s: golden.resolve_doc_targets(doc_rows.get(c.repo, []), s)
                    for s in c.expect_doc},
        }
    return out


def _row_dict(run: EvalRun, *, with_per_query: bool) -> dict:
    out = {"id": run.id, "repo": run.repo, "kind": run.kind, "status": run.status,
           "config": run.config, "metrics": run.metrics, "error": run.error,
           "created_at": run.created_at, "finished_at": run.finished_at}
    if with_per_query:
        out["per_query"] = run.per_query
    return out


async def list_runs(limit: int = 50, offset: int = 0) -> dict:
    async with SessionLocal() as session:
        total = (await session.execute(select(func.count()).select_from(EvalRun))).scalar_one()
        rows = (await session.execute(
            select(EvalRun).order_by(EvalRun.id.desc()).limit(limit).offset(offset))
        ).scalars().all()
    return {"total": int(total), "items": [_row_dict(r, with_per_query=False) for r in rows]}


async def get_run(run_id: int) -> dict | None:
    async with SessionLocal() as session:
        row = (await session.execute(
            select(EvalRun).where(EvalRun.id == run_id))).scalars().first()
    return _row_dict(row, with_per_query=True) if row is not None else None


async def run_and_persist(*, repo: str | None = None,
                          variants: list[dict] | None = None, judge: bool = False,
                          golden_path: str | None = None, trigger: str = "api",
                          persist: bool = True) -> dict:
    """跑一次评测（golden → anchors → 变体×case harness → 聚合 → judge）→ 落账。

    失败不抛：golden 加载/变体构造（I1 修复后也在 try 内）与跑批中途失败均捕获——
    已建 RUNNING 行 → update FAILED；未建行（load 阶段）→ 直接 INSERT 一行 FAILED
    （repo 回落 ``repo or settings.default_repo``、config 标 ``error_stage: load``）；
    ``persist=False`` → 合成 FAILED dict。judge 只评**首个变体**的答案（baseline
    语义；A/B 的答案质量差由主指标承担）。
    """
    path = golden_path or settings.eval_golden_path
    # 预计算（load 失败时 except 分支同样要用）：kind 只数变体个数（构造前后等长）；
    # run_repo 先按 settings 回落，golden 加载成功后再按文件级 repo 精化
    kind = "ab" if len(variants or [{}]) > 1 else "single"
    run_repo = repo or settings.default_repo
    run_id: int | None = None
    rows: list[dict] = []
    try:
        default_repo, cases = golden.load_golden_set(path)
        run_repo = repo or default_repo or settings.default_repo
        fixed = fix_repos(cases, run_repo)
        eval_variants = [EvalVariant(**v) for v in (variants or [{}])]

        if persist:
            async with SessionLocal() as session:
                row = EvalRun(repo=run_repo, kind=kind, status="RUNNING",
                              config={"trigger": trigger, "judge": judge, "golden_path": path,
                                      "variants": [asdict(v) for v in eval_variants]})
                session.add(row)
                await session.commit()
                run_id = row.id

        async with SessionLocal() as session:
            anchors = await resolve_anchors(session, fixed)
        first_evidence: dict[str, CaseEvidence] = {}
        for v in eval_variants:
            for case in fixed:
                ev = await run_case(case, v)
                rows.append(build_row(case, ev, anchors[case.id]["code"],
                                      anchors[case.id]["doc"]))
                if v.name == eval_variants[0].name:
                    first_evidence[case.id] = ev
        agg = {v.name: metrics.aggregate([r for r in rows if r["variant"] == v.name])
               for v in eval_variants}
        metrics_out: dict = {"variants": agg, "judge": None}
        if judge:
            scores = []
            for case in fixed:
                ev = first_evidence.get(case.id)
                scores.append(await judge_case(case.query, ev.answer, ev.citations)
                               if ev is not None and ev.answer else None)
            metrics_out["judge"] = judge_scores(scores)
        if persist:
            async with SessionLocal() as session:
                await session.execute(update(EvalRun).where(EvalRun.id == run_id).values(
                    status="DONE", metrics=metrics_out, per_query=rows,
                    finished_at=datetime.now(UTC)))
                await session.commit()
        else:
            return {"id": None, "repo": run_repo, "kind": kind, "status": "DONE",
                    "config": None, "metrics": metrics_out, "error": None,
                    "created_at": None, "finished_at": None, "per_query": rows}
    except Exception as e:  # noqa: BLE001 —— 评测失败落 FAILED，不向上抛
        logger.warning("eval_service: 评测失败（run_id={}）: {}", run_id, e)
        if persist and run_id is None:
            # load 阶段失败（还没建 RUNNING 行）→ 直接落一行 FAILED（I1：不再 500）
            async with SessionLocal() as session:
                row = EvalRun(repo=run_repo, kind=kind, status="FAILED",
                              config={"trigger": trigger, "error_stage": "load"},
                              error=str(e)[:2000], finished_at=datetime.now(UTC))
                session.add(row)
                await session.commit()
            return _row_dict(row, with_per_query=True)
        if persist:
            async with SessionLocal() as session:
                await session.execute(update(EvalRun).where(EvalRun.id == run_id).values(
                    status="FAILED", error=str(e)[:2000], finished_at=datetime.now(UTC)))
                await session.commit()
        else:
            return {"id": None, "repo": run_repo, "kind": kind, "status": "FAILED",
                    "config": None, "metrics": None, "error": str(e)[:2000],
                    "created_at": None, "finished_at": None, "per_query": rows}
    return await get_run(run_id)


async def reclaim_orphan_runs() -> int:
    """KEEP①：startup 回收 RUNNING 悬挂行（进程中断遗留）→ FAILED。

    单 uvicorn 进程启动时不存在「本进程在跑的评测」，全量回收安全；并行 CLI
    跑批恰逢重启被误标的边缘场景由 error 文案自解释。异常由调用方（lifespan）
    兜住 log，不阻断启动。
    """
    async with SessionLocal() as session:
        result = await session.execute(
            update(EvalRun).where(EvalRun.status == "RUNNING").values(
                status="FAILED", error="进程重启回收：RUNNING 悬挂（进程中断）",
                finished_at=datetime.now(UTC)))
        await session.commit()
        return int(result.rowcount or 0)
