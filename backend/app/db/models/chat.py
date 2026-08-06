"""会话与消息：conversations / chat_messages（智能问答模块持久化）。

设计文档未规定会话表——此处的结构对齐 `api接口清单.md` §2（会话列表/详情/检索详情/
追问/反馈）。检索详情与反馈复用 `retrieval_logs`（已有 recall/coarse/fine/feedback 字段），
通过 chat_messages.retrieval_log_id 关联。
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    """一次问答会话。conversation_id 形如 ``conv_xxx``。"""
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    agent_type: Mapped[str | None] = mapped_column(String(64))
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_conversations_created", "created_at"),
        Index("idx_conversations_agent", "agent_type"),
    )


class ChatMessage(Base):
    """会话内的一条消息（user / assistant）。

    assistant 消息的 ``citations`` 存引用列表；``retrieval_log_id`` 指向
    ``retrieval_logs`` 以提供检索详情（stage1/2/3 漏斗）与反馈。
    """
    __tablename__ = "chat_messages"

    message_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("conversations.conversation_id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[dict | None] = mapped_column(JSONB)  # list[{type,label,...}]
    retrieval_log_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("retrieval_logs.log_id")
    )
    agent_type: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(16), default="completed", server_default="completed", nullable=False
    )  # completed | interrupted（HITL 中断态，M10；resume 后翻回 completed）
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_chat_messages_conversation", "conversation_id"),
        Index("idx_chat_messages_created", "created_at"),
        Index("idx_chat_messages_retrieval", "retrieval_log_id"),
    )
