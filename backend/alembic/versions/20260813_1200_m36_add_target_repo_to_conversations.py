"""m36 add target_repo to conversations

Stage C Milestone 36：领域知识包激活绑定。给 conversations 加 target_repo 列
（nullable，会话绑定的目标仓库标识；null → resolve 回落全局默认仓库）。
nullable add_column：既有行/会话不受影响（默认 null = 通用行为）。

Revision ID: m36targetrepo
Revises: 83d2eacdc0af
Create Date: 2026-08-13 12:00:00+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m36targetrepo"
down_revision: str | None = "83d2eacdc0af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("target_repo", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "target_repo")
