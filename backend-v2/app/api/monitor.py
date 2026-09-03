"""M7 监控 API（Task 10）：/v1/monitor 三端点，全只读。

薄路由：查询都在 :mod:`app.services.monitor_service`；这里只做 session 生命周期 +
**整端点兜底 try/except**（service 内部已按数据段软失败，这层兜的是连 PG 会话都
拿不到的情形）——任何组件失败降级 null/空段，**永不 500**（Global Constraints）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.db.base import SessionLocal
from app.services import monitor_service

router = APIRouter(prefix="/v1/monitor", tags=["monitor"])

_WINDOW = Query(default="7d", pattern="^(today|7d|all)$")


@router.get("/overview")
async def overview(window: str = _WINDOW) -> dict:
    """业务总览（请求量/时延分位/工具与 token 均值/codenav 命中率/路由分布）。"""
    try:
        async with SessionLocal() as session:
            return await monitor_service.overview(session, window=window)
    except Exception as e:  # noqa: BLE001 —— 监控面独立降级，绝不 500
        logger.warning("monitor.overview: 整端点降级: {}", e)
        return {"window": window, "requests": 0, "avg_duration_ms": None, "p50_ms": None,
                "p95_ms": None, "avg_tool_calls": None, "avg_tokens": None,
                "codenav_hit_rate": None, "routes": {}}


@router.get("/traces")
async def traces(
    window: str = _WINDOW,
    limit: int = Query(default=50, ge=1, le=monitor_service.SAMPLE_LIMIT),
) -> dict:
    """全链路追溯列表（id 倒序，total 为窗内总数）。"""
    try:
        async with SessionLocal() as session:
            return await monitor_service.list_traces(session, window=window, limit=limit)
    except Exception as e:  # noqa: BLE001
        logger.warning("monitor.traces: 整端点降级: {}", e)
        return {"window": window, "total": 0, "items": []}


@router.get("/traces/{message_id}")
async def trace_detail(message_id: int) -> dict:
    """单条追溯详情（span 树回放）；无此行 → 404。"""
    try:
        async with SessionLocal() as session:
            detail = await monitor_service.get_trace(session, message_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("monitor.trace_detail: 整端点降级: {}", e)
        return {"message_id": message_id, "conversation_id": None, "query": None,
                "route": None, "legacy": False, "spans": [], "summary": None,
                "created_at": None}
    if detail is None:
        raise HTTPException(status_code=404, detail=f"trace 不存在: message_id={message_id}")
    return detail


@router.get("/pipeline")
async def pipeline() -> dict:
    """离线管道面：Redis 队列深度 + PG 账本计数（Redis 挂 → 对应段 null）。"""
    try:
        async with SessionLocal() as session:
            return await monitor_service.pipeline_stats(session)
    except Exception as e:  # noqa: BLE001
        logger.warning("monitor.pipeline: 整端点降级: {}", e)
        return {"stream": None, "dead": None, "events": None, "last_event_at": None}
