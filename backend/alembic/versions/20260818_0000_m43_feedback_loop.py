"""m43 feedback loop

Revision ID: m43feedback
Revises: m36targetrepo
Create Date: 2026-08-18

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "m43feedback"
down_revision: str | None = "m36targetrepo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # retrieval_logs 扩 2 可空列（旧列零改，monitor/agent_stats 不动）
    op.add_column("retrieval_logs",
                  sa.Column("feedback_categories", postgresql.JSONB(), nullable=True))
    op.add_column("retrieval_logs",
                  sa.Column("feedback_correction", sa.Text(), nullable=True))
    # 候选 eval 集
    op.create_table(
        "candidate_eval_queries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("categories", postgresql.JSONB(), nullable=True),
        sa.Column("correction", sa.Text(), nullable=True),
        sa.Column("source_message_id", sa.String(length=40), nullable=False),
        sa.Column("retrieval_log_id", sa.BigInteger(), nullable=True),
        sa.Column("repo", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CANDIDATE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_message_id"], ["chat_messages.message_id"],
                                name=op.f("fk_candidate_eval_queries_source_message_id_chat_messages")),
        sa.ForeignKeyConstraint(["retrieval_log_id"], ["retrieval_logs.log_id"],
                                name=op.f("fk_candidate_eval_queries_retrieval_log_id_retrieval_logs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidate_eval_queries")),
        sa.UniqueConstraint("source_message_id", name="uk_candidate_eval_queries_source_message"),
    )
    op.create_index(op.f("idx_candidate_eval_queries_status"), "candidate_eval_queries", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("idx_candidate_eval_queries_status"), table_name="candidate_eval_queries")
    op.drop_table("candidate_eval_queries")
    op.drop_column("retrieval_logs", "feedback_correction")
    op.drop_column("retrieval_logs", "feedback_categories")
