"""会话/消息持久化服务（M4）。

约定：
- 所有函数只 flush 不 commit——事务边界归调用方（Task 9 的 SSE 端点在请求结束时提交）。
- session 由调用方注入（生产 = app.db.base.SessionLocal() 的 AsyncSession；
  测试 = 绑定到连接级事务的 AsyncSession，随事务回滚不留痕）。
- 读侧统一走 select() 全量加载，避免 async 会话对过期属性同步访问触发
  MissingGreenlet。
"""
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat import ChatMessage, Conversation, Feedback, _utcnow

TITLE_MAX_CHARS = 50


async def _get_conversation(session: AsyncSession, conversation_id: str) -> Conversation | None:
    row = await session.execute(select(Conversation).where(Conversation.id == conversation_id))
    return row.scalars().first()


async def open_conversation(
    session: AsyncSession,
    *,
    query: str,
    conversation_id: str | None,
    target_repo: str,
) -> tuple[Conversation, str]:
    """取既有会话或新建（id=uuid4().hex，title=query 截 50 字符）。

    传入的 conversation_id 不存在时，沿用该 id 新建（客户端续聊一个已被清理的
    会话不报错，仍拿到可用会话）。
    """
    if conversation_id is not None:
        existing = await _get_conversation(session, conversation_id)
        if existing is not None:
            return existing, existing.id
    cid = conversation_id or uuid4().hex
    conv = Conversation(
        id=cid,
        user_id=None,
        target_repo=target_repo,
        title=(query or "")[:TITLE_MAX_CHARS],
    )
    session.add(conv)
    await session.flush()
    return conv, conv.id


async def add_message(
    session: AsyncSession,
    conv: Conversation,
    *,
    role: str,
    content: str,
    meta: dict | None = None,
) -> int:
    """追加一条消息，flush 取自增 id（不 commit）。

    顺带把会话 updated_at 推进到当前时刻——`list_conversations` 的
    「最近活跃在前」排序靠它；模型 onupdate 只在行被 UPDATE 时生效，
    不主动赋值则老会话补发消息后列表首位不变。
    """
    conv.updated_at = _utcnow()
    msg = ChatMessage(conversation_id=conv.id, role=role, content=content, meta=meta)
    session.add(msg)
    await session.flush()
    return msg.id


async def load_history(
    session: AsyncSession,
    conversation_id: str | None,
    limit: int,
) -> list[dict]:
    """跨轮记忆：最近 ``limit`` 轮（user/assistant **完整成对**），正序 ``[{"role","content"}]``。

    一条 SQL 倒序取末 ``limit*2`` 条 user/assistant 消息再反转（Task 9 streaming 层
    注入 ``state["history"]``，经 ``nodes._history_messages`` 转 langchain 消息）。
    R1（评审 F-A 顺手项）：截断时若把一对切开会留下开头的孤儿 assistant（其 user 已
    落窗外）——丢弃该条，窗口只收完整对。无会话 / ``limit <= 0`` → 空表。
    """
    if not conversation_id or limit <= 0:
        return []
    rows = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id,
               ChatMessage.role.in_(("user", "assistant")))
        .order_by(ChatMessage.id.desc())
        .limit(limit * 2)
    )
    history = [{"role": m.role, "content": m.content}
               for m in reversed(list(rows.scalars().all()))]
    if history and history[0]["role"] == "assistant":
        history = history[1:]
    return history


async def add_feedback(
    session: AsyncSession,
    message_id: int,
    *,
    rating: str,
    comment: str | None,
) -> int:
    """追加一条消息反馈，flush 取自增 id（不 commit——事务边界归调用方）。

    feedback.message_id 故意无外键：消息随会话级联删除后反馈仍独立存活，故
    ``message_id`` 不存在也照收（M9 前无用户体系，不校验消息存在性）。
    """
    fb = Feedback(message_id=message_id, rating=rating, comment=comment)
    session.add(fb)
    await session.flush()
    return fb.id


async def list_conversations(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[Conversation]:
    """会话列表，updated_at 倒序（同刻 updated_at 以 id 升序 tie-break，排序确定）。"""
    stmt = (select(Conversation)
            .order_by(Conversation.updated_at.desc(), Conversation.id)
            .limit(limit).offset(offset))
    rows = await session.execute(stmt)
    return list(rows.scalars().all())


async def get_conversation_detail(session: AsyncSession, conversation_id: str) -> dict | None:
    """会话详情 + 全部消息（按 id 升序）；无此会话返回 None。"""
    conv = await _get_conversation(session, conversation_id)
    if conv is None:
        return None
    rows = await session.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation_id).order_by(ChatMessage.id)
    )
    messages = rows.scalars().all()
    return {
        "conversation": {
            "id": conv.id,
            "user_id": conv.user_id,
            "target_repo": conv.target_repo,
            "title": conv.title,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
        },
        "messages": [
            {
                "id": m.id,
                "conversation_id": m.conversation_id,
                "role": m.role,
                "content": m.content,
                "meta": m.meta,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }
