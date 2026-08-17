"""Agent 面板聚合统计服务（Phase 7 Milestone 7，api接口清单 §七 Agent 面板）。

**只读，无新表**——所有指标按需聚合自 ``retrieval_logs``（落库时已写入完整漏斗 meta +
``agent_steps`` + ``user_feedback``）：

- ``recall_results->>'mode'='agent'``：Agent 成功跑完的可靠信号（仅 ``_base._emit_retrieval_meta``
  写入；``_degrade`` 会用 ``pipeline.recall`` 的 meta 覆盖、丢 ``mode``/``agent`` 键）。
- ``agent_steps``（独立 JSONB 列）：工具轨迹。M41 三形状——旧 list ``[{tool,args,n},...]``、
  新 dict ``{"version":2,"spans":[{kind,...}]}``、``NULL``。空表持久化为 NULL。
- ``user_feedback``（``HELPFUL``/``NOT_HELPFUL``）：满意度。
- 降级（部分失败）= ``_HAS_TOOLS AND mode IS NULL``；分母「Agent 曾介入」
  = ``mode='agent' OR _HAS_TOOLS``（_HAS_TOOLS 三形状：旧非空 list / 新 dict 含 tool span）。

降级 run 丢 ``recall_results->>'agent'`` 标签 → ``LEFT JOIN chat_messages``（同词表
``CODE_UNDERSTAND``/``DOC_ANSWER``/``CHANGE_IMPACT``/``BUG_DIAGNOSIS``），以
``coalesce(meta->>'agent', chat_messages.agent_type)`` 恢复归属。

分层：纯 helper（无 DB，单测覆盖）+ async 查询层（取 ``AsyncSession``，ORM ``select`` + GROUP BY）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, literal, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, RetrievalLog
from app.schemas.agent import (
    AgentRunItem,
    AgentRunsResponse,
    AgentStatRow,
    AgentStatsResponse,
)

# recall_results JSONB 访问（仿 sync.py:122 change_details["type"].astext）
_MODE = RetrievalLog.recall_results["mode"].astext
_AGENT = RetrievalLog.recall_results["agent"].astext
# 助手消息（每条 retrieval_log 对应一条 assistant 消息）——用于降级 run 的 agent 标签回退
_ASSIST = and_(
    ChatMessage.retrieval_log_id == RetrievalLog.log_id,
    ChatMessage.role == "assistant",
)
# 标签：成功 run 用 meta.agent；降级 run（meta 丢 agent）回退 chat_messages.agent_type
_LABEL = func.coalesce(_AGENT, ChatMessage.agent_type).label("agent")

# M41 三形状：「有工具步」谓词——旧 list 非空 / 新 dict 含 kind=="tool" 的 span
_STEPS = RetrievalLog.agent_steps
_STYPE = func.jsonb_typeof(_STEPS)
_HAS_TOOLS = or_(
    and_(_STYPE == "array", func.jsonb_array_length(_STEPS) > 0),
    and_(_STYPE == "object",
         func.jsonb_array_length(func.jsonb_path_query_array(
             _STEPS, literal('$.spans[*] ? (@.kind == "tool")'))) > 0),
)
# 工具步数（步数均值用）：array → 长度；object → tool span 数；其余 NULL
_STEPS_LEN = case(
    (_STYPE == "array", func.jsonb_array_length(_STEPS)),
    (_STYPE == "object", func.jsonb_array_length(func.jsonb_path_query_array(
        _STEPS, literal('$.spans[*] ? (@.kind == "tool")')))),
    else_=None,
)

# 「Agent 曾介入」：成功 run 或 发过若干步后兜底的 run（降级率分母）
_ENGAGED = or_(_MODE == "agent", _HAS_TOOLS)
# 「部分失败降级」：发过若干步后兜底但 mode 被 _degrade 覆盖丢失
_DEGRADED = and_(_HAS_TOOLS, _MODE.is_(None))


# ============================================================================
# 纯 helper（无 DB，单测覆盖）
# ============================================================================


def _ratio(num: int, den: int) -> float | None:
    """安全比率：分母为 0 → None（面板「无数据」），否则保留 4 位。"""
    return round(num / den, 4) if den else None


def _since(window: str) -> datetime | None:
    """窗口起点（UTC）：today=今日 0 点；7d=7 天前；all=None。"""
    now = datetime.now(UTC)
    if window == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "7d":
        return now - timedelta(days=7)
    return None


def _time_filter(since: datetime | None):
    return RetrievalLog.created_at >= since if since else true()


# ============================================================================
# async 查询层
# ============================================================================


async def get_agent_stats(session: AsyncSession, window: str = "today") -> AgentStatsResponse:
    """Agent 面板 KPI（窗口内）+ 各 Agent 明细（GROUP BY 标签）。

    KPI 用单条条件聚合（``sum(case(...))`` / ``avg(case(...))``）一次取全；per_agent 单独 GROUP BY。
    """
    since = _since(window)
    tf = _time_filter(since)

    # ---- KPI：单条聚合（仿 sync.py sync_status 的 func.count 思路，加 case 分支）----
    kpi = (await session.execute(
        select(
            func.sum(case((_MODE == "agent", 1), else_=0)),  # total_calls
            func.sum(case((_ENGAGED, 1), else_=0)),  # engaged
            func.sum(case((_DEGRADED, 1), else_=0)),  # degraded
            func.sum(case((RetrievalLog.user_feedback == "HELPFUL", 1), else_=0)),  # helpful
            func.count(RetrievalLog.user_feedback),  # feedback（非空计数）
            # avg 仅对 mode='agent' 行取 jsonb_array_length；NULL 自动被 avg 忽略
            func.avg(case((_MODE == "agent", _STEPS_LEN), else_=None)),
        ).where(tf)
    )).one()
    total_calls = int(kpi[0] or 0)
    engaged = int(kpi[1] or 0)
    degraded = int(kpi[2] or 0)
    helpful = int(kpi[3] or 0)
    feedback = int(kpi[4] or 0)
    avg_steps = round(float(kpi[5]), 2) if kpi[5] is not None else None

    # ---- per_agent：按标签 GROUP BY（LEFT JOIN 恢复降级 run 标签）----
    rows = (await session.execute(
        select(
            _LABEL,
            func.count(),  # calls
            func.avg(case((_MODE == "agent", _STEPS_LEN), else_=None)),  # avg_steps
            func.avg(case((RetrievalLog.fine_rank_count > 0, 1), else_=0)),  # hit_rate
            func.sum(case((RetrievalLog.user_feedback == "HELPFUL", 1), else_=0)),  # helpful
            func.count(RetrievalLog.user_feedback),  # feedback
            func.sum(case((_DEGRADED, 1), else_=0)),  # degraded
        )
        .select_from(RetrievalLog)
        .outerjoin(ChatMessage, _ASSIST)
        .where(and_(_ENGAGED, tf))
        .group_by(_LABEL)
        .order_by(func.count().desc())
    )).all()
    per_agent: list[AgentStatRow] = []
    for agent, calls, avg_s, hit, hlp, fb, deg in rows:
        per_agent.append(AgentStatRow(
            agent=agent or "UNKNOWN",
            calls=int(calls or 0),
            avg_steps=round(float(avg_s), 2) if avg_s is not None else None,
            hit_rate=round(float(hit), 4) if hit is not None else None,
            satisfaction=_ratio(int(hlp or 0), int(fb or 0)),
            degraded=int(deg or 0),
        ))

    return AgentStatsResponse(
        window=window,
        total_calls=total_calls,
        engaged=engaged,
        degraded=degraded,
        degradation_rate=_ratio(degraded, engaged),
        avg_steps=avg_steps,
        helpful=helpful,
        feedback=feedback,
        satisfaction=_ratio(helpful, feedback),
        per_agent=per_agent,
    )


async def get_agent_runs(session: AsyncSession, pg: dict) -> AgentRunsResponse:
    """最近 Agent 运行流水（分页，仿 sync.py list_tasks：q 取行 + cq 取 total，同 where）。"""
    base = (
        select(
            RetrievalLog.log_id,
            RetrievalLog.created_at,
            _LABEL,
            RetrievalLog.query_text,
            func.coalesce(_STEPS_LEN, 0),  # steps（NULL→0）
            func.coalesce(RetrievalLog.fine_rank_count, 0),  # citations
            _DEGRADED,  # bool
            RetrievalLog.user_feedback,
        )
        .select_from(RetrievalLog)
        .outerjoin(ChatMessage, _ASSIST)
        .where(_ENGAGED)
        .order_by(RetrievalLog.created_at.desc())
    )
    rows = (await session.execute(base.offset(pg["offset"]).limit(pg["page_size"]))).all()
    total = (await session.execute(
        select(func.count(RetrievalLog.log_id)).where(_ENGAGED)
    )).scalar_one()
    items = [AgentRunItem(
        log_id=r[0],
        created_at=r[1],
        agent=r[2],
        query=(r[3] or "")[:60],
        steps=int(r[4] or 0),
        citations=int(r[5] or 0),
        degraded=bool(r[6]),
        feedback=r[7],
    ) for r in rows]
    return AgentRunsResponse(total=total, items=items)
