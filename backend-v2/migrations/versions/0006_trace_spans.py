"""全链路追溯表 trace_spans（M7 Task 8）。

Revision ID: v2_0006
Revises: v2_0005
Create Date: 2026-09-03

手写迁移（非 autogenerate）：显式 create_table + UNIQUE(message_id)（每条 assistant
消息至多一棵 span 树）+ (conversation_id)/(created_at) 索引，与 ORM 逐列对应。
spec §4.1 偏差：原写 log_id/request_id——v2 无 retrieval_logs，落为按 message_id
一比一（FK→chat_messages ON DELETE CASCADE + UNIQUE）。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "v2_0006"
down_revision = "v2_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trace_spans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.String(32), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("route", sa.String(32), nullable=False),
        sa.Column("spans", JSONB, nullable=True),  # 冻结平面 span 列表（SpanCollector.to_dict()）
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("token_usage", JSONB, nullable=True),  # CostController.to_meta() 原样
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("message_id", name="uk_trace_spans_message_id"),
    )
    op.create_index("ix_trace_spans_conversation_id", "trace_spans", ["conversation_id"])
    op.create_index("ix_trace_spans_created_at", "trace_spans", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_trace_spans_created_at", table_name="trace_spans")
    op.drop_index("ix_trace_spans_conversation_id", table_name="trace_spans")
    op.drop_table("trace_spans")
