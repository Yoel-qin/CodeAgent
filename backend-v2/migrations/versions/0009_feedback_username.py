"""KEEP②：feedback 加 username 归属列（字符串冗余、无外键——对齐本表
message_id 无 FK 约定：用户删除后反馈独立存活；历史行 NULL）。

Revision ID: v2_0009
Revises: v2_0008
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op

revision = "v2_0009"
down_revision = "v2_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feedback", sa.Column("username", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("feedback", "username")
