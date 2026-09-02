"""Chat 路由（Plan 3 Task 9）：SSE 流式问答 + 会话列表/详情。

``event_gen`` 沿旧库 ``api/v1/chat.py`` 模式逐字：``stream_chat`` 的
``(event, data)`` 对 → ``{"event": ..., "data": json.dumps(data, ensure_ascii=False)}``。
SSE 层不做任何业务判断；空 query 400 与 conversation_id 422 是仅有的两个前置闸。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from app.agent.streaming import stream_chat
from app.db.base import SessionLocal
from app.schemas.chat import ChatRequest
from app.services import chat_service

router = APIRouter(prefix="/v1/chat", tags=["chat"])


@router.post("/completions")
async def completions(req: ChatRequest):
    """SSE 流式问答：事件 conversation / retrieval / citation / token / agent_step / done。"""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")

    async def event_gen():
        async with SessionLocal() as session:
            async for event, data in stream_chat(
                session, query=req.query, conversation_id=req.conversation_id,
                repo=req.repo, top_k=req.top_k,
            ):
                yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}

    return EventSourceResponse(event_gen())


@router.get("/conversations")
async def list_conversations(
    limit: int = Query(default=50, ge=0),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """会话列表（updated_at 倒序，最近活跃在前）；limit/offset 负值 → 422（防 PG 报错 500）。"""
    async with SessionLocal() as session:
        rows = await chat_service.list_conversations(session, limit=limit, offset=offset)
    return [{"id": c.id, "title": c.title, "target_repo": c.target_repo,
             "created_at": c.created_at, "updated_at": c.updated_at} for c in rows]


@router.get("/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str) -> dict:
    """会话详情 + 全部消息；不存在 → 404（不暴露存在性歧义）。"""
    async with SessionLocal() as session:
        detail = await chat_service.get_conversation_detail(session, conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return detail
