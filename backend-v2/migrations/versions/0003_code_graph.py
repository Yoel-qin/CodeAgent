"""调用图三表：code_entities / call_edges / code_metrics（M3）。

Revision ID: v2_0003
Revises: v2_0002
Create Date: 2026-09-02
"""

import sqlalchemy as sa
from alembic import op

revision = "v2_0003"
down_revision = "v2_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_entities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("class_name", sa.String(512), nullable=False),
        sa.Column("method_name", sa.String(256), nullable=True),
        sa.Column("module", sa.String(256), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("start_line", sa.Integer, nullable=True),
        sa.Column("end_line", sa.Integer, nullable=True),
        sa.Column("signature", sa.String(2048), nullable=True),
    )
    op.create_index("ix_code_entities_repo", "code_entities", ["repo"])
    op.create_index("ix_code_entities_repo_class_name", "code_entities", ["repo", "class_name"])
    op.create_unique_constraint(
        "uk_code_entities_identity",
        "code_entities",
        ["repo", "class_name", "method_name", "file_path", "start_line"],
    )

    op.create_table(
        "call_edges",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "caller_id",
            sa.Integer,
            sa.ForeignKey("code_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "callee_id",
            sa.Integer,
            sa.ForeignKey("code_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_type", sa.String(32), nullable=False),
        sa.Column("call_site_file", sa.String(1024), nullable=False),
        sa.Column("call_site_line", sa.Integer, nullable=False),
    )
    op.create_index("ix_call_edges_caller_id", "call_edges", ["caller_id"])
    op.create_index("ix_call_edges_callee_id", "call_edges", ["callee_id"])
    op.create_unique_constraint(
        "uk_call_edges_caller_id_callee_id_call_site_line",
        "call_edges",
        ["caller_id", "callee_id", "call_site_line"],
    )

    op.create_table(
        "code_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "entity_id",
            sa.Integer,
            sa.ForeignKey("code_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("complexity", sa.Integer, nullable=False),
        sa.Column("fan_in", sa.Integer, nullable=False),
        sa.Column("fan_out", sa.Integer, nullable=False),
        sa.Column("loc", sa.Integer, nullable=False),
    )
    op.create_unique_constraint(
        "uk_code_metrics_entity_id",
        "code_metrics",
        ["entity_id"],
    )


def downgrade() -> None:
    op.drop_table("code_metrics")
    op.drop_table("call_edges")
    op.drop_table("code_entities")
