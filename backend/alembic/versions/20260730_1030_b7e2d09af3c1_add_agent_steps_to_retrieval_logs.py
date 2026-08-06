"""add agent_steps to retrieval_logs

Phase 7 Milestone 5：Agent 步骤可观测性。给 retrieval_logs 加 agent_steps JSONB 列，
存场景 Agent 的工具调用轨迹（[{tool, args, n}, ...]）；legacy/retrieve 路径为 NULL。
复用既有漏斗表（同 recall_results / fine_rank_results 的 JSONB 模式），不另建表。

Revision ID: b7e2d09af3c1
Revises: c8d3ea5f2b17
Create Date: 2026-07-30 10:30:00+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7e2d09af3c1'
down_revision: str | None = 'c8d3ea5f2b17'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'retrieval_logs',
        sa.Column('agent_steps', postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('retrieval_logs', 'agent_steps')
