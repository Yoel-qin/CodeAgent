"""Chat 路由（Plan 3 Task 9）：SSE 流式问答 + 会话列表/详情。

``event_gen`` 沿旧库 ``api/v1/chat.py`` 模式逐字：``stream_chat`` 的
``(event, data)`` 对 → ``{"event": ..., "data": json.dumps(data, ensure_ascii=False)}``。
SSE 层不做任何业务判断；空 query 400 与 conversation_id 422 是仅有的两个前置闸。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from app.agent.streaming import stream_chat
from app.api.deps import (
    ensure_repo_allowed,
    get_current_user,
    repo_visible,
    request_scopes,
    require_class,
)
from app.core.config import settings
from app.db.base import SessionLocal
from app.schemas.chat import ChatRequest, FeedbackRequest
from app.services import chat_service

router = APIRouter(prefix="/v1/chat", tags=["chat"], dependencies=[Depends(require_class("chat"))])


@router.post("/completions")
async def completions(req: ChatRequest, user: dict = Depends(get_current_user)):
    """SSE 流式问答：事件 conversation / retrieval / citation / token / agent_step / done。

    M9：RBAC on 时 repo 门（不可见 → 403）+ scopes 注入图内三路门；off 全直通。
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")
    ensure_repo_allowed(user, req.repo or settings.default_repo)

    async def event_gen():
        async with SessionLocal() as session:
            async for event, data in stream_chat(
                session, query=req.query, conversation_id=req.conversation_id,
                repo=req.repo, top_k=req.top_k, scopes=request_scopes(user),
            ):
                yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}

    return EventSourceResponse(event_gen())


@router.get("/conversations")
async def list_conversations(
    limit: int = Query(default=50, ge=0),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
) -> list[dict]:
    """会话列表（updated_at 倒序，最近活跃在前）；limit/offset 负值 → 422（防 PG 报错 500）。

    终审 I-1：RBAC on 且可见仓库非 ``"*"`` 时按可见集过滤 ``target_repo``
    （同 documents 列表的 repos_filter 模式——过滤在 SQL 层，limit/offset 分页
    语义不被逐行过滤破坏）；off / ``"*"`` → repos_filter 恒 None 零行为变更。
    空 ``target_repo`` 不在可见集 → 不进列表（fail-closed，同 IN 语义）。
    """
    repos_filter: list[str] | None = None
    if settings.rbac_enabled:
        allowed = (user.get("allowed_scopes") or {}).get("repos") or []
        if "*" not in allowed:
            repos_filter = sorted(allowed)
    async with SessionLocal() as session:
        rows = await chat_service.list_conversations(
            session, limit=limit, offset=offset, repos=repos_filter)
    return [{"id": c.id, "title": c.title, "target_repo": c.target_repo,
             "created_at": c.created_at, "updated_at": c.updated_at} for c in rows]


@router.get("/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str, user: dict = Depends(get_current_user)) -> dict:
    """会话详情 + 全部消息；不存在 → 404（不暴露存在性歧义）。

    终审 I-1：RBAC on 时不可见 ``target_repo`` 的会话同判 404（不暴露存在性，
    同 documents sections 模式）——assistant 正文含 file:line 代码引用，历史读
    通道不得旁路图内门；off 态 ``repo_visible`` 恒 True 零行为变更。
    """
    async with SessionLocal() as session:
        detail = await chat_service.get_conversation_detail(session, conversation_id)
    if detail is not None and not repo_visible(user, detail["conversation"]["target_repo"]):
        detail = None
    if detail is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return detail


@router.post("/messages/{message_id}/feedback")
async def message_feedback(message_id: int, body: FeedbackRequest,
                           user: dict = Depends(get_current_user)) -> dict:
    """消息反馈落库（M6 Task 3；KEEP② 记录归属用户名——off 落 "anonymous"）。

    feedback 无外键——message_id 不存在也接受；rating 非法 / comment 超
    2000 字 → 422（pydantic 校验）。事务边界在本端点：service 只 flush。
    """
    async with SessionLocal() as session:
        feedback_id = await chat_service.add_feedback(
            session, message_id, rating=body.rating, comment=body.comment,
            username=user["username"],
        )
        await session.commit()
    return {"ok": True, "feedback_id": feedback_id}
