"""主动腐化巡检模块路由（M16 报告 / M17 SWEEP 批量重写审批队列）。

聚合 ``chunk_relations.is_stale``（DOC↔CODE 关系级过时表征，区别于 ``/sync/status`` 数的
``doc_chunks.stale_anchors`` JSONB），按来源（SWEEP 巡检 / DELETED soft_delete / other）分解 +
最近巡检发现。M17 起另提供：为 top-N SWEEP 过时 doc 批量生成重写提案、列出提案（审批队列）、
逐项 approve/reject。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, pagination
from app.core.config import settings
from app.schemas.staleness import (
    ProposalDecisionRequest,
    ProposalDecisionResponse,
    ProposalItem,
    ProposalListResponse,
    StalenessFinding,
    StalenessReport,
    StalenessSourceBreakdown,
    SweepRewriteRequest,
    SweepRewriteResponse,
)
from app.services import staleness_sweep_service, sweep_rewrite_service

router = APIRouter(prefix="/staleness", tags=["staleness"])


@router.get("/report", response_model=StalenessReport)
async def staleness_report(
    session: AsyncSession = Depends(get_db),
    recent: int = Query(20, ge=1, le=100),
) -> StalenessReport:
    """DOC↔CODE 过时关系报告：总数/过时数 + 来源分解（SWEEP/DELETED/other）+ 最近巡检发现。"""
    data = await staleness_sweep_service.build_staleness_report(session, recent=recent)
    return StalenessReport(
        total=data["total"],
        stale=data["stale"],
        by_source=StalenessSourceBreakdown(**data["by_source"]),
        recent=[StalenessFinding(**f) for f in data["recent"]],
    )


@router.post("/sweep-rewrite", response_model=SweepRewriteResponse)
async def sweep_rewrite(
    body: SweepRewriteRequest,
    session: AsyncSession = Depends(get_db),
) -> SweepRewriteResponse:
    """为 top-N SWEEP 标记的过时 doc 批量生成重写提案（落 doc_update_proposals PENDING 行＝审批队列）。

    复用 M15 ``generate_doc_update`` / ``create_doc_pr``；按 doc_chunk_id 去重 + 幂等守卫（已有 active
    提案的段落跳过）。直接 await（generate async；顺序执行 N 次，故 top_n cap 低）。
    """
    data = await sweep_rewrite_service.run_sweep_rewrite(
        session, top_n=min(body.top_n, settings.sweep_rewrite_top_n_max)
    )
    return SweepRewriteResponse(**data)


@router.get("/proposals", response_model=ProposalListResponse)
async def list_proposals(
    session: AsyncSession = Depends(get_db),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> ProposalListResponse:
    """列出 doc 更新提案（审批队列），可选 status 过滤，按 created_at 倒序分页。"""
    pg = pagination(page, page_size)
    data = await sweep_rewrite_service.list_proposals(
        session, status=status, offset=pg["offset"], limit=pg["page_size"]
    )
    return ProposalListResponse(
        total=data["total"], items=[ProposalItem(**it) for it in data["items"]]
    )


@router.post("/proposals/{proposal_id}/decide", response_model=ProposalDecisionResponse)
async def decide_proposal(
    proposal_id: int,
    body: ProposalDecisionRequest,
    session: AsyncSession = Depends(get_db),
) -> ProposalDecisionResponse:
    """人工审批单条提案：approve→APPROVED（**真写回** doc_chunks + 清关系 + 懒重嵌入）；reject→REJECTED。"""
    data = await sweep_rewrite_service.set_proposal_status(
        session, proposal_id=proposal_id, status=body.decision
    )
    if data.get("error") == "not found":
        raise HTTPException(status_code=404, detail="提案不存在")
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"])
    return ProposalDecisionResponse(**data)
