"""评测运行账本 eval_runs（M8 Task 1）。

Revision ID: v2_0007
Revises: v2_0006
Create Date: 2026-09-04

手写迁移（非 autogenerate，沿 v2_0006 模式）：显式 create_table，与 ORM 逐列对应。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "v2_0007"
down_revision = "v2_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("metrics", JSONB, nullable=True),
        sa.Column("per_query", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_eval_runs_repo", "eval_runs", ["repo"])
    op.create_index("ix_eval_runs_created_at", "eval_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_eval_runs_created_at", table_name="eval_runs")
    op.drop_index("ix_eval_runs_repo", table_name="eval_runs")
    op.drop_table("eval_runs")
