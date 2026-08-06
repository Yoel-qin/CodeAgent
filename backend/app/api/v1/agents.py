"""Agent 面板路由（api接口清单 §七）：聚合统计 + 最近运行流水。

纯读路径，走 AsyncSession；逻辑在 ``services/agent_stats_service``（无新表，按需聚合
``retrieval_logs``）。窗口参数：today / 7d / all。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, pagination
from app.schemas.agent import AgentRunsResponse, AgentStatsResponse
from app.services import agent_stats_service

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/stats", response_model=AgentStatsResponse)
async def agent_stats(
    window: str = Query("today", pattern="today|7d|all"),
    session: AsyncSession = Depends(get_db),
) -> AgentStatsResponse:
    """Agent 面板 KPI（调用/满意度/平均步骤/降级率）+ 各 Agent 明细。"""
    return await agent_stats_service.get_agent_stats(session, window)


@router.get("/runs", response_model=AgentRunsResponse)
async def agent_runs(
    session: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AgentRunsResponse:
    """最近 Agent 运行流水（分页）。"""
    pg = pagination(page, page_size)
    return await agent_stats_service.get_agent_runs(session, pg)
