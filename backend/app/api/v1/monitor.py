"""系统监控路由（Phase 8.4，api接口清单 §八）：检索性能 / API 用量 / 索引规模 / 资源。

纯读路径，走 AsyncSession；逻辑在 ``services/monitor_service``（无新表/迁移/依赖，
全聚合既有数据源）。窗口参数（仅 retrieval-perf / api-usage）：today / 7d / all。
外部组件（Milvus/ES/Redis/MinIO）失败 → 该字段 None / ``up:false``，端点不 500。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.monitor import (
    ApiUsageResponse,
    IndexStatsResponse,
    ResourcesResponse,
    RetrievalPerfResponse,
)
from app.services import monitor_service

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
