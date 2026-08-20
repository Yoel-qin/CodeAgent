"""智能问答模块路由（SSE 流式，设计 §2.1 / 技术栈 §9）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.schemas.chat import ChatRequest, ContinueRequest, ResumeRequest
from app.services.chat_service import get_message_status, stream_chat

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/completions")
async def completions(
    req: ChatRequest, session: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """SSE 流式问答：事件 retrieval / citation / token / done。"""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")

    async def event_gen():
        async for event, data in stream_chat(
            session, req.query, top_k=req.top_k, agent_type=req.agent_type,
            conversation_id=req.conversation_id, user=user,
        ):
            yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}

    return EventSourceResponse(event_gen())


@router.post("/resume")
async def resume(
    req: ResumeRequest, session: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """HITL 续跑（M10）：对 interrupted 态消息给出人工决策，续跑主图并流式产出 token → done。

    仅 ``RAG_ENGINE=langgraph`` 可用（中断态来自主图 checkpoint）；legacy 模式返回 501。
    """
    if settings.rag_engine != "langgraph":
        raise HTTPException(status_code=501, detail="HITL 仅在 RAG_ENGINE=langgraph 下可用")

    # M45 属主校验（404）——在 409 校验之前
    from app.services.chat_service import get_owned_conversation
    await get_owned_conversation(session, req.conversation_id, user)

    # 校验消息仍处待审批态：过期（HITL 超时，M14）/已完成/不存在 → 409，干净失败而非静默 no-op。
    status = await get_message_status(session, req.message_id)
    if status != "interrupted":
        raise HTTPException(
            status_code=409, detail=f"该消息不在待审批状态（当前 status={status}）",
        )

    # 延迟导入：避免 chat ↔ agent.streaming 循环，且 legacy/非 langgraph 路径零开销。
    from app.agent.streaming import resume_graph

    decision = {"approved": req.approved, "comment": req.comment}

    async def event_gen():
        async for event, data in resume_graph(
            session, conversation_id=req.conversation_id,
            message_id=req.message_id, decision=decision, user=user,
        ):
            yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}

    return EventSourceResponse(event_gen())


@router.post("/continue")
async def continue_turn(
    req: ContinueRequest, session: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """通用续跑（M14 Part C）：推进一条已存在 thread 的执行。

    仅 ``RAG_ENGINE=langgraph`` 可用（断流恢复 / 中断态上报）；legacy → 501。
    """
    if settings.rag_engine != "langgraph":
        raise HTTPException(status_code=501, detail="通用续跑仅在 RAG_ENGINE=langgraph 下可用")

    from app.agent.streaming import continue_graph

    async def event_gen():
        async for event, data in continue_graph(
            session, conversation_id=req.conversation_id, message_id=req.message_id,
            user=user,
        ):
            yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}

    return EventSourceResponse(event_gen())
