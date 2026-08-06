"""drop graph_embedding vector columns

图向量（graph_vec / 路径 C / Phase 5 GNN）已移除；删 graph_embeddings 表的向量列，
保留结构特征列（pagerank/degree/community_id_*）供将来 GraphRAG 复用。

Revision ID: a3f1c08e9b42
Revises: 768492a1d1e5
Create Date: 2026-07-27 10:30:00+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3f1c08e9b42'
down_revision: str | None = '768492a1d1e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('graph_embeddings', 'graph_embedding')
    op.drop_column('graph_embeddings', 'embedding_dim')


def downgrade() -> None:
    op.add_column(
        'graph_embeddings',
        sa.Column('embedding_dim', sa.Integer(), nullable=False, server_default='256'),
    )
    op.add_column(
        'graph_embeddings',
        sa.Column('graph_embedding', sa.LargeBinary(), nullable=True),
    )
