"""智能问答 — 会话/检索详情/追问/反馈 模块路由（api接口清单 §2.2–2.6）。"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, pagination
from app.clients.llm_client import llm
from app.core.config import settings
from app.db.models.chat import ChatMessage, Conversation
from app.db.models.system import RetrievalLog
from app.schemas.conversation import (
    AgentTraceResponse,
    ConversationDetailResponse,
    ConversationItem,
    ConversationListResponse,
    FeedbackRequest,
    InterruptInfo,
    MessageItem,
    RetrievalDetailResponse,
    SuggestionRequest,
    SuggestionResponse,
    ThreadStateResponse,
)

router = APIRouter(prefix="/chat", tags=["chat"])


def _build_agent_trace(
    *, agent_steps: list | None, agent_type: str | None,
) -> AgentTraceResponse | None:
    """有 Agent 工具调用轨迹才返回 agent 段。

    判定用 ``agent_steps`` 非空（而非 meta.mode）：``_degrade`` 会用真实漏斗覆盖
    ``retrieval_meta`` 丢 ``"agent"`` 键，但若 Agent 降级前已跑几步，``agent_steps``
    已累积，此时仍应展示「它试过这些工具」更合理。``type`` 取 ``msg.agent_type``
    （降级覆盖 meta 后仍可靠）。
    """
    if not agent_steps:
        return None
    return AgentTraceResponse(type=agent_type or "AGENT", steps=agent_steps)


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    session: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    agent_type: str | None = Query(None),
) -> ConversationListResponse:
    pg = pagination(page, page_size)
    q = select(Conversation).order_by(Conversation.created_at.desc())
    cq = select(func.count()).select_from(Conversation)
    if agent_type:
        q = q.where(Conversation.agent_type == agent_type)
        cq = cq.where(Conversation.agent_type == agent_type)
    rows = (await session.execute(q.offset(pg["offset"]).limit(pg["page_size"]))).scalars().all()
    total = (await session.execute(cq)).scalar_one()
    items = [
        ConversationItem(
            conversation_id=r.conversation_id, title=r.title, agent_type=r.agent_type,
            message_count=r.message_count, created_at=r.created_at, updated_at=r.updated_at,
        )
        for r in rows
    ]
    return ConversationListResponse(total=total, items=items)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str, session: AsyncSession = Depends(get_db),
) -> ConversationDetailResponse:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    rows = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at)
        )
    ).scalars().all()
    messages = [
        MessageItem(
            message_id=m.message_id, role=m.role, content=m.content,
            citations=m.citations, agent_type=m.agent_type, created_at=m.created_at,
            status=m.status,
        )
        for m in rows
    ]
    return ConversationDetailResponse(
        conversation_id=conv.conversation_id, title=conv.title,
        agent_type=conv.agent_type, messages=messages,
    )


@router.get("/conversations/{conversation_id}/state", response_model=ThreadStateResponse)
async def get_thread_state(
    conversation_id: str, session: AsyncSession = Depends(get_db),
) -> ThreadStateResponse:
    """会话线程执行状态（HITL 可观测，M14 Part C）：是否有待审批 interrupt + 最新 assistant 消息状态。

    仅 ``RAG_ENGINE=langgraph`` 可用（线程状态来自主图 checkpoint）；legacy → 501。
    """
    if settings.rag_engine != "langgraph":
        raise HTTPException(status_code=501, detail="线程状态仅在 RAG_ENGINE=langgraph 下可用")
    from app.agent.graph import get_graph
    from app.agent.streaming import _extract_interrupts

    config = {"configurable": {"thread_id": conversation_id, "session": session}}
    snap = await get_graph().aget_state(config)
    interrupts = _extract_interrupts(snap)

    latest = (await session.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        .where(ChatMessage.role == "assistant").order_by(ChatMessage.created_at.desc()).limit(1)
    )).scalars().first()

    interrupt_info: InterruptInfo | None = None
    if interrupts:
        proposal = interrupts[0].value
        imsg = (await session.execute(
            select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
            .where(ChatMessage.status == "interrupted")
            .order_by(ChatMessage.created_at.desc()).limit(1)
        )).scalars().first()
        age = None
        if imsg and imsg.created_at:
            age = round((datetime.now(UTC) - imsg.created_at).total_seconds() / 3600, 2)
        interrupt_info = InterruptInfo(
            proposal=str(proposal) if proposal is not None else None,
            message_id=imsg.message_id if imsg else None,
            created_at=imsg.created_at if imsg else None,
            age_hours=age,
        )
    return ThreadStateResponse(
        conversation_id=conversation_id, status=latest.status if latest else None,
        has_pending_interrupt=bool(interrupts), interrupt=interrupt_info,
    )


@router.get("/messages/{message_id}/retrieval", response_model=RetrievalDetailResponse)
async def get_retrieval(
    message_id: str, session: AsyncSession = Depends(get_db),
) -> RetrievalDetailResponse:
    """返回该消息的检索漏斗（stage1 召回+RRF / stage2 粗排 / stage3 精排候选）。"""
    msg = await session.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    if not msg.retrieval_log_id:
        raise HTTPException(status_code=404, detail="该消息无检索详情")
    rlog = await session.get(RetrievalLog, msg.retrieval_log_id)
    if rlog is None:
        raise HTTPException(status_code=404, detail="检索日志不存在")

    meta = rlog.recall_results or {}
    recall = meta.get("recall", {})
    stage1 = {
        "latency_ms": meta.get("recall_ms"),
        "channels": [
            {"name": "vector", "count": recall.get("vector", meta.get("vector", 0))},
            {"name": "bm25", "count": recall.get("lexical", meta.get("lexical", 0))},
            {"name": "graph_traverse", "count": recall.get("graph", meta.get("graph", 0))},
        ],
        "merged_count": meta.get("rrf_pool", meta.get("merged", 0)),
        "terms": meta.get("terms", []),
    }
    stage2 = {
        "model": settings.reranker_coarse_model or None,
        "latency_ms": None,
        "output_count": meta.get("coarse"),
    }
    stage3 = {
        "model": settings.reranker_fine_model,
        "latency_ms": meta.get("rerank_ms"),
        "output_count": meta.get("fine"),
        "rerank_on": meta.get("rerank_on"),
        "results": rlog.fine_rank_results or [],
    }
    agent = _build_agent_trace(agent_steps=rlog.agent_steps, agent_type=msg.agent_type)
    return RetrievalDetailResponse(stage1=stage1, stage2=stage2, stage3=stage3, agent=agent)


@router.post("/suggestions", response_model=SuggestionResponse)
async def suggest(
    req: SuggestionRequest, session: AsyncSession = Depends(get_db),
) -> SuggestionResponse:
    """基于会话最近消息生成 3 条追问建议；未配置 LLM 或失败则返回空。"""
    rows = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == req.conversation_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(4)
        )
    ).scalars().all()
    msgs = list(reversed(rows))
    if not msgs or not llm.configured:
        return SuggestionResponse(suggestions=[])
    transcript = "\n".join(
        f"{'用户' if m.role == 'user' else '助手'}：{m.content[:300]}" for m in msgs
    )
    prompt = [
        {"role": "system", "content": "你是代码知识库助手。基于对话生成 3 个用户可能想追问的简短"
                                      "中文问题，每行一个，不要编号、不要多余解释。"},
        {"role": "user", "content": f"对话：\n{transcript}\n\n生成 3 个追问："},
    ]
    try:
        text = await llm.chat(prompt, temperature=0.5, max_tokens=256)
    except Exception:  # noqa: BLE001
        return SuggestionResponse(suggestions=[])
    lines = [ln.strip("0123456789.、-.) \t").strip() for ln in text.splitlines()]
    return SuggestionResponse(suggestions=[ln for ln in lines if ln][:3])


@router.post("/messages/{message_id}/feedback")
async def feedback(
    message_id: str, req: FeedbackRequest, session: AsyncSession = Depends(get_db),
) -> dict:
    """消息反馈（HELPFUL/NOT_HELPFUL）写入关联 retrieval_logs（为 Phase 8 LTR 攒数据）。"""
    if req.rating not in {"HELPFUL", "NOT_HELPFUL"}:
        raise HTTPException(status_code=400, detail="rating 必须为 HELPFUL 或 NOT_HELPFUL")
    msg = await session.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    persisted = False
    if msg.retrieval_log_id:
        rlog = await session.get(RetrievalLog, msg.retrieval_log_id)
        if rlog is not None:
            rlog.user_feedback = req.rating
            rlog.feedback_time = datetime.now(UTC)
            persisted = True
    await session.commit()
    return {"ok": True, "feedback": req.rating, "persisted": persisted}
