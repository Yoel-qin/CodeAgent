"""m45 rbac: roles/users + conversations.user_id + 4 built-in roles seed

Revision ID: m45rbac
Revises: m43feedback
Create Date: 2026-08-20

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "m45rbac"
down_revision: str | None = "m43feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_SEED = [
    ("admin", "管理员：全部权限 + 用户管理", '["*"]', '["*"]'),
    ("developer", "开发者：全部 chunk + 读写维护 + eval",
     '["code", "doc", "table", "image"]',
     '["chat", "search", "graph", "readops", "writeops"]'),
    ("ops", "运维：全部 chunk + 只读运维", '["code", "doc", "table", "image"]',
     '["chat", "search", "graph", "readops"]'),
    ("external", "外部：仅文档类 chunk，不可见源码", '["doc", "table", "image"]',
     '["chat", "search"]'),
]


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("allowed_kinds", postgresql.JSONB(), nullable=False),
        sa.Column("endpoint_classes", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("name", name="uk_roles_name"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name=op.f("fk_users_role_id_roles")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name="uk_users_username"),
    )
    # conversations.user_id（可空；off 时期历史为 NULL 共享）
    op.add_column("conversations",
                  sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_conversations_user_id_users", "conversations",
                          "users", ["user_id"], ["id"])
    # 内置角色幂等 seed（不 seed 用户：避免默认弱密码入库，首管理员经 create_user.py 创建）
    for name, desc, kinds, classes in _ROLE_SEED:
        op.execute(
            sa.text(
                "INSERT INTO roles (name, description, allowed_kinds, endpoint_classes) "
                "VALUES (:name, :desc, cast(:kinds as jsonb), cast(:classes as jsonb)) "
                "ON CONFLICT (name) DO NOTHING"
            ).bindparams(name=name, desc=desc, kinds=kinds, classes=classes)
        )


def downgrade() -> None:
    op.drop_constraint("fk_conversations_user_id_users", "conversations", type_="foreignkey")
    op.drop_column("conversations", "user_id")
    op.drop_table("users")
    op.drop_table("roles")
