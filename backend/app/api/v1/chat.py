"""智能问答模块路由（SSE 流式，设计 §2.1 / 技术栈 §9）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_db
from app.schemas.chat import ChatRequest
from app.services.chat_service import stream_chat

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/completions")
async def completions(req: ChatRequest, session: AsyncSession = Depends(get_db)):
    """SSE 流式问答：事件 retrieval / citation / token / done。"""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")

    async def event_gen():
        async for event, data in stream_chat(
            session, req.query, top_k=req.top_k, agent_type=req.agent_type,
            conversation_id=req.conversation_id,
        ):
            yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}

    return EventSourceResponse(event_gen())
