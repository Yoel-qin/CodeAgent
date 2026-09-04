"""RBAC：roles + users + 4 内置角色 seed（M9 Task 8）。

Revision ID: v2_0008
Revises: v2_0007
Create Date: 2026-09-04

手写迁移（沿 v2_0006/0007 模式）。seed 幂等（ON CONFLICT DO NOTHING）——
外部角色 external 只可见 doc 域、端点限 chat/repos/documents/reader（无 graph/sync）。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "v2_0008"
down_revision = "v2_0007"
branch_labels = None
depends_on = None

_ROLES = [
    ("admin", '{"repos": ["*"], "kinds": ["code", "doc"]}', '["*"]'),
    ("developer", '{"repos": ["*"], "kinds": ["code", "doc"]}', '["*"]'),
    ("ops", '{"repos": ["*"], "kinds": ["code", "doc"]}',
     '["chat", "repos", "documents", "graph", "reader", "sync", "monitor", "eval"]'),
    ("external", '{"repos": ["*"], "kinds": ["doc"]}',
     '["chat", "repos", "documents", "reader"]'),
]


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("allowed_scopes", JSONB, nullable=False),
        sa.Column("endpoint_classes", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("name", name="uk_roles_name"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("username", name="uk_users_username"),
    )
    op.create_index("ix_users_role_id", "users", ["role_id"])
    for name, scopes, classes in _ROLES:
        op.execute(
            f"INSERT INTO roles (name, allowed_scopes, endpoint_classes) "
            f"VALUES ('{name}', '{scopes}'::jsonb, '{classes}'::jsonb) "
            f"ON CONFLICT (name) DO NOTHING"
        )


def downgrade() -> None:
    op.drop_index("ix_users_role_id", table_name="users")
    op.drop_table("users")
    op.drop_table("roles")
