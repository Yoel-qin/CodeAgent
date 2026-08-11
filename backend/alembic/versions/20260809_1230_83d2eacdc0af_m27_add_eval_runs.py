"""m27 add eval runs

Revision ID: 83d2eacdc0af
Revises: 14d0bbf2b0ab
Create Date: 2026-08-09 12:30:48.212531+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '83d2eacdc0af'
down_revision: str | None = '14d0bbf2b0ab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("run_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("trigger", sa.String(length=32), nullable=False, server_default="api"),
        sa.Column("top_k", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("rewrite", sa.String(length=16), nullable=False, server_default="off"),
        sa.Column("embedding_strategy", sa.String(length=32), nullable=False, server_default="unified"),
        sa.Column("n_queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_evaluable", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rerank_on_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("aggregate", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("config", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("per_query", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("unresolved", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_eval_runs")),
    )
    op.create_index(op.f("idx_eval_runs_created"), "eval_runs", ["created_at"], unique=False)
    op.create_index(op.f("idx_eval_runs_strategy"), "eval_runs", ["embedding_strategy"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("idx_eval_runs_strategy"), table_name="eval_runs")
    op.drop_index(op.f("idx_eval_runs_created"), table_name="eval_runs")
    op.drop_table("eval_runs")
