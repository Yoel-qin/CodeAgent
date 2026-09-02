"""管道状态表 pipeline_events（M5 Task 12）。

Revision ID: v2_0005
Revises: v2_0004
Create Date: 2026-09-03

手写迁移（非 autogenerate）：显式 create_table + UNIQUE(repo, commit_hash, path) +
(repo, status) 复合索引，与 ORM 逐列对应。
"""

import sqlalchemy as sa
from alembic import op

revision = "v2_0005"
down_revision = "v2_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("commit_hash", sa.String(64), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),  # graph_rebuild 固定 "__repo__"
        sa.Column("event_kind", sa.String(32), nullable=False),  # file | graph_rebuild
        sa.Column("status", sa.String(16), nullable=False),  # PENDING|DONE|DEAD
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.UniqueConstraint(
            "repo", "commit_hash", "path", name="uk_pipeline_events_repo_commit_hash_path"
        ),
    )
    op.create_index("ix_pipeline_events_repo_status", "pipeline_events", ["repo", "status"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_events_repo_status", table_name="pipeline_events")
    op.drop_table("pipeline_events")
