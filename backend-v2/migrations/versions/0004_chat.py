"""会话三表：conversations / chat_messages / feedback（M4）。

Revision ID: v2_0004
Revises: v2_0003
Create Date: 2026-09-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "v2_0004"
down_revision = "v2_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(32), primary_key=True),  # uuid4().hex，应用侧生成
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("target_repo", sa.String(256), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(32),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("meta", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"])

    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("message_id", sa.Integer, nullable=True),  # 故意无外键，消息删除后反馈独立存活
        sa.Column("rating", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_feedback_message_id", "feedback", ["message_id"])


def downgrade() -> None:
    op.drop_table("feedback")
    op.drop_table("chat_messages")
    op.drop_table("conversations")
