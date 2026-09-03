"""全链路追溯表 ORM：TraceSpan（M7）。

实现说明（spec §4.1 偏差，见 Plan Global Constraints）：
- spec 原写 ``trace_spans(log_id, request_id, ...)``——v2 无 retrieval_logs，改为按
  assistant 消息一比一落行：``message_id`` FK→chat_messages.id ON DELETE CASCADE 且
  UNIQUE（每条消息至多一棵 span 树；消息随会话级联删除时 trace 一并消失）。
- ``spans`` 是 JSONB 平面列表（元素形状 = SpanCollector 冻结字典形状，TraceView 直接消费）；
  ``token_usage`` 存 ``CostController.to_meta()`` 原样。
- meta 类 JSONB 同样走 ``JSON().with_variant(JSONB, "postgresql")``（与 chat.py meta 同款）。
- created_at 除 server_default 外补 Python 侧 default：flush 后属性即在内存中可用，
  避免 async 会话访问 server-generated 属性触发惰性加载（MissingGreenlet）。
"""
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_pg_jsonb = JSON().with_variant(JSONB, "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TraceSpan(Base):
    __tablename__ = "trace_spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        unique=True,  # uk_trace_spans_message_id：每条 assistant 消息至多一棵 span 树
    )
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    query: Mapped[str] = mapped_column(Text)
    route: Mapped[str] = mapped_column(String(32))  # codenav|docqa|graphnav|clarify|chitchat|...
    spans: Mapped[list | None] = mapped_column(_pg_jsonb, nullable=True)  # 冻结平面 span 列表
    duration_ms: Mapped[int] = mapped_column(Integer)  # request span 时长取整
    token_usage: Mapped[dict | None] = mapped_column(_pg_jsonb, nullable=True)  # cost.to_meta()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=_utcnow,
        index=True,  # ix_trace_spans_created_at：监控端点按时间窗聚合
    )
