"""系统监控路由（Phase 8.4，api接口清单 §八）：检索性能 / API 用量 / 索引规模 / 资源。

纯读路径，走 AsyncSession；逻辑在 ``services/monitor_service``（无新表/迁移/依赖，
全聚合既有数据源）。窗口参数（仅 retrieval-perf / api-usage）：today / 7d / all。
外部组件（Milvus/ES/Redis/MinIO）失败 → 该字段 None / ``up:false``，端点不 500。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.monitor import (
    ApiUsageResponse,
    FeedbackReportResponse,
    IndexStatsResponse,
    ResourcesResponse,
    RetrievalPerfResponse,
    TraceDetail,
    TraceListResponse,
)
from app.services import monitor_service
from app.services.feedback_service import build_feedback_report

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/retrieval-perf", response_model=RetrievalPerfResponse)
async def retrieval_perf(
    window: str = Query("today", pattern="today|7d|all"),
    session: AsyncSession = Depends(get_db),
) -> RetrievalPerfResponse:
    """检索性能漏斗：延迟分布 p50/p95 + 召回/精排漏斗均值 + 精排率 + 反馈计数。"""
    return await monitor_service.get_retrieval_perf(session, window)


@router.get("/api-usage", response_model=ApiUsageResponse)
async def api_usage(
    window: str = Query("today", pattern="today|7d|all"),
    session: AsyncSession = Depends(get_db),
) -> ApiUsageResponse:
    """外部 API 用量（查询侧 PG 派生代理，详见响应 ``note``）。"""
    return await monitor_service.get_api_usage(session, window)


@router.get("/index-stats", response_model=IndexStatsResponse)
async def index_stats(
    session: AsyncSession = Depends(get_db),
) -> IndexStatsResponse:
    """各索引/表规模：PG 行数 + Milvus 向量数 + ES 文档数。"""
    return await monitor_service.get_index_stats(session)


@router.get("/resources", response_model=ResourcesResponse)
async def resources(
    session: AsyncSession = Depends(get_db),
) -> ResourcesResponse:
    """基础设施连通与占用（PG/Redis/Milvus/ES/MinIO）。"""
    return await monitor_service.get_resources(session)


# ---- GET /monitor/traces（M41 全链路追溯）----


@router.get("/traces", response_model=TraceListResponse)
async def traces(
    window: str = Query("today", pattern="today|7d|all"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> TraceListResponse:
    """M41 全链路追溯：请求列表（span 汇总，最新在前）。"""
    return await monitor_service.get_traces(session, window, limit)


@router.get("/traces/{log_id}", response_model=TraceDetail)
async def trace_detail(
    log_id: int,
    session: AsyncSession = Depends(get_db),
) -> TraceDetail:
    """单请求 span 树（新 dict 原样 / 旧行伪 span 合成）。缺失 404。"""
    d = await monitor_service.get_trace(session, log_id)
    if d is None:
        raise HTTPException(status_code=404, detail="检索日志不存在")
    return d


# ---- GET /monitor/feedback-report（M43 反馈闭环）----


@router.get("/feedback-report", response_model=FeedbackReportResponse)
async def feedback_report(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_db),
) -> FeedbackReportResponse:
    """M43 反馈闭环报告：分类分布 / repo 分组 / 关键词 / 幻觉告警（×M34 enforcement 交叉）。"""
    return FeedbackReportResponse(**await build_feedback_report(session, days=days))
