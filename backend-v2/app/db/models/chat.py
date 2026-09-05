"""会话三表 ORM：Conversation / ChatMessage / Feedback（M4）。

实现说明：
- conversation.id 由应用侧 uuid4().hex 生成（32 位字符串主键），非 DB 自增。
- meta 用 SQLAlchemy 通用 JSON 类型 + PG 方言 JSONB variant（与 doc.parse_meta 同款）。
- created_at/updated_at 除 server_default 外补 Python 侧 default/onupdate：
  flush 后属性即在内存中可用，避免 async 会话访问 server-generated 属性触发
  惰性加载（MissingGreenlet）。
- feedback.message_id 故意不加外键（brief 接口如此）：消息随会话级联删除时
  反馈记录独立存活，便于后续做全局满意度统计。
"""
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_pg_jsonb = JSON().with_variant(JSONB, "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid4().hex
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    target_repo: Mapped[str] = mapped_column(String(256))
    title: Mapped[str] = mapped_column(String(512))  # 首条 query 截 50 字符
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        default=_utcnow,
        onupdate=_utcnow,
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user|assistant
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(_pg_jsonb, nullable=True)  # citations/agent_steps/intent/route
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=_utcnow
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int | None] = mapped_column(Integer, index=True)  # 无外键，见模块 docstring
    rating: Mapped[str] = mapped_column(String(16))  # HELPFUL|NOT_HELPFUL
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 反馈者：RBAC off → "anonymous"；历史行 NULL；无外键同 message_id 约定
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=_utcnow
    )
