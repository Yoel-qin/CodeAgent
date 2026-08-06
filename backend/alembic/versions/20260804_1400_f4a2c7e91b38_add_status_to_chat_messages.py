"""add status to chat_messages

Phase 7 Milestone 10：HITL 人在回路中断。给 chat_messages 加 status 列
（completed | interrupted），标记 HITL 中断态消息；resume 续跑后翻回 completed。
server_default='completed' 让既有行 ALTER 安全、既有 insert 不受影响（默认 completed）。

Revision ID: f4a2c7e91b38
Revises: b7e2d09af3c1
Create Date: 2026-08-04 14:00:00+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4a2c7e91b38'
down_revision: str | None = 'b7e2d09af3c1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'chat_messages',
        sa.Column('status', sa.String(length=16), nullable=False, server_default='completed'),
    )


def downgrade() -> None:
    op.drop_column('chat_messages', 'status')
