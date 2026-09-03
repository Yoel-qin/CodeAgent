"""M7 监控服务（Task 10）：overview / traces / pipeline 三面只读聚合。

设计（沿 v1 monitor 模式 + Plan Global Constraints「监控端点独立降级」）：

- 每个函数内的**每个数据段**（SQL 段 / Python 样本段 / codenav 引用段 / Redis 段 /
  PG 管道段）各自 try/except：组件失败 → 该段回退 null/空并 log，**永不向上抛**；
  API 层再兜一层整端点 try/except（返回全 null 骨架），双保险。
- 只读：全部 select；session 由调用方注入（生产 = ``app.db.base.SessionLocal``，
  测试 = 连接级事务回滚会话），函数自身不 commit。
- ``overview`` 双段口径（brief 契约）：SQL 段单查询 COUNT/AVG/p50/p95 按 ``window``
  过滤；Python 段取**最近 ≤500 行**样本（route/spans/message_id/token_usage，不过滤
  时间窗）算 avg_tool_calls / routes / avg_tokens / codenav_hit_rate——codenav 命中率
  的分母只数 codenav 行，meta 经一条 ``IN`` 查询取回。
- Redis 段同步直调（``_redis_stream_stats``：XLEN/XINFO GROUPS 两次小 IO，不值得
  to_thread；独立成模块函数便于测试钉）。
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from statistics import fmean

import redis as redis_lib
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.chat import ChatMessage
from app.db.models.pipeline import PipelineEvent
from app.db.models.trace import TraceSpan

# overview 样本段 / traces 列表的行数上限（brief：Python 段取最近 ≤500 行）
SAMPLE_LIMIT = 500


def _cutoff(window: str) -> datetime | None:
    """window → ``created_at`` 下界；``all`` → None（无条件）。today 按 UTC 零点截断
    （与容器内 PG 的 UTC 时钟一致；created_at 均为 timestamptz）。"""
    now = datetime.now(UTC)
    if window == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "7d":
        return now - timedelta(days=7)
    return None


def _f(v: object) -> float | None:
    """PG 数值（int avg 回 numeric/Decimal）归一 float | None。"""
    return float(v) if v is not None else None  # type: ignore[return-value]


def _n_tool_calls(spans: object) -> int:
    """spans 平面列表里 ``kind=="tool"`` 的 span 数（脏形/None 防御为 0）。"""
    if not isinstance(spans, list):
        return 0
    return sum(1 for s in spans if isinstance(s, dict) and s.get("kind") == "tool")


def _token_usage(tu: object) -> dict | None:
    """token_usage → 冻结三键形状；无 spent_tokens 视为无用量 → None。"""
    if not isinstance(tu, dict) or not isinstance(tu.get("spent_tokens"), (int, float)):
        return None
    return {"spent_tokens": tu.get("spent_tokens"), "llm_calls": tu.get("llm_calls"),
            "estimated": tu.get("estimated")}


async def overview(session: AsyncSession, *, window: str) -> dict:
    """业务总览：请求量 / 时延分位 / 工具与 token 均值 / codenav 命中率 / 路由分布。"""
    out: dict = {"window": window, "requests": 0, "avg_duration_ms": None, "p50_ms": None,
                 "p95_ms": None, "avg_tool_calls": None, "avg_tokens": None,
                 "codenav_hit_rate": None, "routes": {}}
    cutoff = _cutoff(window)

    # ── SQL 段（单查询：COUNT + AVG + percentile_cont p50/p95，按 window 过滤）──
    try:
        stmt = select(
            func.count(),
            func.avg(TraceSpan.duration_ms),
            func.percentile_cont(0.5).within_group(TraceSpan.duration_ms),
            func.percentile_cont(0.95).within_group(TraceSpan.duration_ms),
        )
        if cutoff is not None:
            stmt = stmt.where(TraceSpan.created_at >= cutoff)
        n, avg_dur, p50, p95 = (await session.execute(stmt)).one()
        out.update(requests=int(n or 0), avg_duration_ms=_f(avg_dur),
                   p50_ms=_f(p50), p95_ms=_f(p95))
    except Exception as e:  # noqa: BLE001 —— 监控面独立降级，绝不 500
        logger.warning("monitor.overview: SQL 段降级: {}", e)

    # ── Python 段（最近 ≤500 行样本，不过滤时间窗——brief 契约）──
    try:
        rows = (await session.execute(
            select(TraceSpan.route, TraceSpan.spans, TraceSpan.message_id, TraceSpan.token_usage)
            .order_by(TraceSpan.id.desc()).limit(SAMPLE_LIMIT))).all()
        if rows:
            out["avg_tool_calls"] = fmean(_n_tool_calls(spans) for _r, spans, _m, _t in rows)
            out["routes"] = dict(Counter(route for route, _s, _m, _t in rows))
            spent = [float(tu["spent_tokens"]) for _r, _s, _m, tu in rows
                     if isinstance(tu, dict) and isinstance(tu.get("spent_tokens"), (int, float))]
            out["avg_tokens"] = fmean(spent) if spent else None
            # codenav 命中率：分母只数 codenav 行；这批 message 的 meta 一条 IN 查询取回
            codenav_ids = [mid for route, _s, mid, _t in rows if route == "codenav"]
            if codenav_ids:
                metas = (await session.execute(
                    select(ChatMessage.meta).where(ChatMessage.id.in_(codenav_ids))
                )).scalars().all()
                hits = sum(1 for meta in metas
                           if any(isinstance(c, dict) and c.get("kind") == "code"
                                  for c in (meta or {}).get("citations") or []))
                out["codenav_hit_rate"] = hits / len(codenav_ids)
    except Exception as e:  # noqa: BLE001
        logger.warning("monitor.overview: 样本段降级: {}", e)
    return out


async def list_traces(session: AsyncSession, *, window: str, limit: int) -> dict:
    """追溯列表（id 倒序）：total 为窗内总数，items 截 limit 条。"""
    out: dict = {"window": window, "total": 0, "items": []}
    try:
        cutoff = _cutoff(window)
        count_stmt = select(func.count()).select_from(TraceSpan)
        page_stmt = select(TraceSpan).order_by(TraceSpan.id.desc()).limit(limit)
        if cutoff is not None:
            cond = TraceSpan.created_at >= cutoff
            count_stmt, page_stmt = count_stmt.where(cond), page_stmt.where(cond)
        out["total"] = int((await session.execute(count_stmt)).scalar_one() or 0)
        rows = (await session.execute(page_stmt)).scalars().all()
        out["items"] = [
            {"message_id": t.message_id, "query": t.query, "route": t.route,
             "total_ms": t.duration_ms, "tokens": _token_usage(t.token_usage),
             "n_tool_calls": _n_tool_calls(t.spans), "created_at": t.created_at}
            for t in rows
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("monitor.list_traces: 降级: {}", e)
    return out


async def get_trace(session: AsyncSession, message_id: int) -> dict | None:
    """单条追溯详情（span 树原样回放）；无此行 → None（API 层 404）。"""
    row = (await session.execute(
        select(TraceSpan).where(TraceSpan.message_id == message_id))).scalars().first()
    if row is None:
        return None
    return {
        "message_id": row.message_id,
        "conversation_id": row.conversation_id,
        "query": row.query,
        "route": row.route,
        "legacy": False,  # v2 一比一落行，恒为 v2 形状（字段留作 v1 兼容位）
        "spans": list(row.spans or []),
        "summary": {"total_ms": row.duration_ms, "tokens": _token_usage(row.token_usage),
                    "n_spans": len(row.spans or [])},
        "created_at": row.created_at,
    }


def _redis_stream_stats() -> dict:
    """Redis 段：主流（XLEN + XINFO GROUPS 首组的 pending/lag）与死信流各自独立
    try/except——任一异常（Redis 挂 / 流或组不存在）只把对应段降级 None。
    同步直调（monitor 低频读，两次小 IO 不值得 to_thread）。"""
    out = {"stream": None, "dead": None}
    r = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        try:
            groups = r.xinfo_groups(settings.pipe_stream) or []
            g = groups[0] if groups else {}
            out["stream"] = {
                "length": int(r.xlen(settings.pipe_stream)),
                "pending": int(g.get("pending") or 0),
                # lag 键 Redis 7.0+ 才有（XINFO GROUPS）：缺键 → None
                "lag": g.get("lag") if "lag" in g else None,
                "group": settings.pipe_group,
            }
        except Exception as e:  # noqa: BLE001 —— 流/组不存在（NOGROUP）等 → 段降级
            logger.warning("monitor.pipeline: 主流段降级: {}", e)
        try:
            out["dead"] = {"length": int(r.xlen(settings.pipe_dead_stream))}
        except Exception as e:  # noqa: BLE001
            logger.warning("monitor.pipeline: 死信段降级: {}", e)
    finally:
        r.close()  # 一次性连接即用即关（本服务不持连接池单例）
    return out


async def pipeline_stats(session: AsyncSession) -> dict:
    """离线管道面：Redis 队列深度（软失败）+ PG 账本按 status 计数与最近活动时间。"""
    out: dict = {"stream": None, "dead": None, "events": None, "last_event_at": None}
    try:
        out.update(_redis_stream_stats())
    except Exception as e:  # noqa: BLE001 —— Redis 整体挂 → stream/dead 双段 null
        logger.warning("monitor.pipeline: Redis 段降级: {}", e)
    try:
        rows = (await session.execute(
            select(PipelineEvent.status, func.count()).group_by(PipelineEvent.status))).all()
        counts = {"PENDING": 0, "DONE": 0, "DEAD": 0}
        for status, n in rows:
            counts[status] = counts.get(status, 0) + int(n)
        out["events"] = counts
        out["last_event_at"] = (await session.execute(
            select(func.max(PipelineEvent.updated_at)))).scalar_one()
    except Exception as e:  # noqa: BLE001
        logger.warning("monitor.pipeline: PG 段降级: {}", e)
    return out
