"""检索评测路由（api接口清单 §eval / Phase 9 评测产品化 M27 / A/B 消融 M28）。

- ``POST /run``：同步跑一轮真实检索评测（``run_eval`` 全漏斗）并落 ``EvalRun``，返完整 detail。
  对齐 ``sync/trigger``（同步跑完返 COMPLETED/FAILED）；``run_eval`` 全 async 故无需 to_thread。
  重型端点（~85 query × 重排 API ≈ 数十秒）——前端单请求 300s 超时。
- ``GET /runs``：历史列表（轻，无 per_query）；``kind`` 查询参可过滤单次/A-B。
- ``GET /runs/{run_id}``：单条详情（含 per_query），缺失 404。
- A/B 消融（M28）：``POST /ab`` + ``GET /ab-runs[/{id}]``，落 ``config.kind="ab"`` 行。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.db.models.eval import EvalRun
from app.schemas.eval import (
    ABPairResult,
    ABRunDetail,
    ABRunListResponse,
    ABRunRequest,
    ABRunSummary,
    ABVariantResult,
    EvalAggregate,
    EvalRunDetail,
    EvalRunListResponse,
    EvalRunRequest,
    EvalRunSummary,
)
from app.services import eval_run_service

router = APIRouter(prefix="/eval", tags=["eval"])


def _kind(run: EvalRun) -> str:
    """``config.kind`` 区分键；单次评测无该键 → "single"。"""
    return (run.config or {}).get("kind", "single")


def _common(run: EvalRun) -> dict:
    """Summary/Detail 共用的字段映射。aggregate 经 ``EvalAggregate`` 校验；unresolved_count 派生。"""
    return dict(
        run_id=run.run_id,
        status=run.status,
        trigger=run.trigger,
        top_k=run.top_k,
        rewrite=run.rewrite,
        embedding_strategy=run.embedding_strategy,
        n_queries=run.n_queries,
        n_evaluable=run.n_evaluable,
        rerank_on_count=run.rerank_on_count,
        duration_ms=run.duration_ms,
        unresolved_count=len(run.unresolved or []),
        aggregate=EvalAggregate.model_validate(run.aggregate) if run.aggregate else None,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        error_message=run.error_message,
        kind=_kind(run),
        ablation=(run.config or {}).get("ablation"),  # M29: 单次评测变体配置（None=全开）
    )


def _to_summary(run: EvalRun) -> EvalRunSummary:
    return EvalRunSummary(**_common(run))


def _to_detail(run: EvalRun) -> EvalRunDetail:
    return EvalRunDetail(**_common(run), config=run.config, per_query=run.per_query, unresolved=run.unresolved)


@router.post("/run", response_model=EvalRunDetail)
async def run(
    body: EvalRunRequest,
    session: AsyncSession = Depends(get_db),
) -> EvalRunDetail:
    """触发一次评测（默认持久化）。``persist=False`` 预览不写库，但仍返完整 detail。
    ``ablation``（M29）：跑单变体（如 ``{"rerank": False}``）；未知字段 → 422。"""
    try:
        run = await eval_run_service.run_and_persist(
            session,
            top_k=body.top_k,
            rewrite=body.rewrite,
            eval_set=body.eval_set,
            ablation=body.ablation,
            persist=body.persist,
        )
    except ValueError as e:  # 未知 ablation 字段 → 422
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _to_detail(run)


@router.get("/runs", response_model=EvalRunListResponse)
async def list_runs(
    limit: int = Query(50, ge=1, le=200),
    kind: str | None = Query(None, pattern="^(single|ab)$"),
    session: AsyncSession = Depends(get_db),
) -> EvalRunListResponse:
    """评测历史列表（最新在前；不含 per_query）。``kind`` 可过滤单次/A-B。"""
    runs = await eval_run_service.list_runs(session, limit=limit, kind=kind)
    return EvalRunListResponse(total=len(runs), items=[_to_summary(r) for r in runs])


@router.get("/runs/{run_id}", response_model=EvalRunDetail)
async def get_run(
    run_id: int,
    session: AsyncSession = Depends(get_db),
) -> EvalRunDetail:
    """单条评测详情（含 per_query 明细）。"""
    run = await eval_run_service.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="评测任务不存在")
    return _to_detail(run)


# ===== A/B 消融（M28）=====


def _ab_pairs(report: dict) -> list[ABPairResult]:
    return [ABPairResult(**p) for p in (report.get("pairs") or [])]


def _ab_variants(report: dict) -> dict[str, ABVariantResult]:
    out: dict[str, ABVariantResult] = {}
    for name, v in (report.get("variants") or {}).items():
        agg = v.get("aggregate")
        out[name] = ABVariantResult(
            ablation=v.get("ablation") or {},
            desc=v.get("desc", ""),
            aggregate=EvalAggregate.model_validate(agg) if agg else None,
            n_evaluable=v.get("n_evaluable", 0),
            n_queries=v.get("n_queries", 0),
            rerank_on_count=v.get("rerank_on_count", 0),
            unresolved=v.get("unresolved", 0),
            per_query=v.get("per_query"),
        )
    return out


def _to_ab_summary(run: EvalRun) -> ABRunSummary:
    report = (run.config or {}).get("report") or {}
    base = _common(run)
    return ABRunSummary(
        **{k: base[k] for k in ABRunSummary.model_fields if k in base},
        pairs=_ab_pairs(report),
    )


def _to_ab_detail(run: EvalRun) -> ABRunDetail:
    report = (run.config or {}).get("report") or {}
    base = _common(run)
    return ABRunDetail(
        **{k: base[k] for k in ABRunDetail.model_fields if k in base},
        pairs=_ab_pairs(report),
        variants=_ab_variants(report),
        config=run.config,
    )


@router.post("/ab", response_model=ABRunDetail)
async def run_ab_endpoint(
    body: ABRunRequest,
    session: AsyncSession = Depends(get_db),
) -> ABRunDetail:
    """触发一次 A/B 消融（默认持久化为 ``config.kind="ab"`` 行）。重型端点：3~4 变体 × ~85 query ×
    全漏斗 ≈ 数十秒~分钟级，前端单请求 300s+ 超时。``diagnose=True`` 时详情含向量路诊断字段。"""
    try:
        run = await eval_run_service.run_ab_and_persist(
            session,
            top_k=body.top_k,
            rewrite=body.rewrite,
            eval_set=body.eval_set,
            pairs=body.pairs,
            graph_subset=body.graph_subset,
            diagnose=body.diagnose,
            persist=body.persist,
        )
    except ValueError as e:  # 未知 pair 名 → 422
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _to_ab_detail(run)


@router.get("/ab-runs", response_model=ABRunListResponse)
async def list_ab_runs(
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> ABRunListResponse:
    """A/B 历史列表（最新在前；轻量，无 variants per_query）。"""
    runs = await eval_run_service.list_runs(session, limit=limit, kind="ab")
    items = [_to_ab_summary(r) for r in runs]
    return ABRunListResponse(total=len(items), items=items)


@router.get("/ab-runs/{run_id}", response_model=ABRunDetail)
async def get_ab_run(
    run_id: int,
    diagnose: bool = Query(False),
    session: AsyncSession = Depends(get_db),
) -> ABRunDetail:
    """单条 A/B 详情（含 variants + pairs delta）。``diagnose=True`` 才返回变体 per_query 的
    ``recall_paths``/``retrieved_kinds``（持久化时已按运行时 diagnose 裁剪，此参仅对未裁剪行生效）。"""
    run = await eval_run_service.get_run(session, run_id)
    if run is None or _kind(run) != "ab":
        raise HTTPException(status_code=404, detail="评测任务不存在")
    return _to_ab_detail(run)
