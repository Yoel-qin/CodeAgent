"""drop graphrag community tables and columns

GraphRAG 社区摘要功能整体弃用（2026-07-29）：不再实现社区检测 / 社区摘要 / 全局社区问答。
删除 graph_communities、node_community_mapping 两表，以及 graph_embeddings 上的
community_id_l0/l1/l2 三列与对应索引。graph_embeddings 表（pagerank/degree/betweenness
通用结构特征）保留。社区功能从未写入数据，故无数据损失。

Revision ID: c8d3ea5f2b17
Revises: a3f1c08e9b42
Create Date: 2026-07-29 09:45:00+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c8d3ea5f2b17'
down_revision: str | None = 'a3f1c08e9b42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # graph_embeddings：删社区列与索引（表保留）
    op.drop_index('idx_graph_embed_community_l0', table_name='graph_embeddings')
    op.drop_index('idx_graph_embed_community_l1', table_name='graph_embeddings')
    op.drop_column('graph_embeddings', 'community_id_l0')
    op.drop_column('graph_embeddings', 'community_id_l1')
    op.drop_column('graph_embeddings', 'community_id_l2')
    # node_community_mapping（FK -> graph_communities），先于 graph_communities 删
    op.drop_index('idx_node_community', table_name='node_community_mapping')
    op.drop_index('idx_community_nodes', table_name='node_community_mapping')
    op.drop_table('node_community_mapping')
    # graph_communities
    op.drop_index('idx_community_level', table_name='graph_communities')
    op.drop_table('graph_communities')


def downgrade() -> None:
    # 还原 graph_communities
    op.create_table(
        'graph_communities',
        sa.Column('community_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=True),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('node_count', sa.Integer(), nullable=False),
        sa.Column('edge_count', sa.Integer(), nullable=False),
        sa.Column('member_chunk_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('summary_embedding_synced', sa.Boolean(), nullable=False),
        sa.Column('computed_at_commit', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('community_id', name=op.f('pk_graph_communities')),
    )
    op.create_index('idx_community_level', 'graph_communities', ['level'], unique=False)
    # 还原 node_community_mapping
    op.create_table(
        'node_community_mapping',
        sa.Column('chunk_id', sa.String(length=128), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('community_id', sa.BigInteger(), nullable=False),
        sa.Column('is_centroid', sa.Boolean(), nullable=False),
        sa.Column('pagerank_in_community', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ['community_id'],
            ['graph_communities.community_id'],
            name=op.f('fk_node_community_mapping_community_id_graph_communities'),
        ),
        sa.PrimaryKeyConstraint('chunk_id', 'level', name=op.f('pk_node_community_mapping')),
    )
    op.create_index('idx_community_nodes', 'node_community_mapping', ['community_id'], unique=False)
    op.create_index('idx_node_community', 'node_community_mapping', ['chunk_id'], unique=False)
    # 还原 graph_embeddings 社区列与索引
    op.add_column('graph_embeddings', sa.Column('community_id_l0', sa.BigInteger(), nullable=True))
    op.add_column('graph_embeddings', sa.Column('community_id_l1', sa.BigInteger(), nullable=True))
    op.add_column('graph_embeddings', sa.Column('community_id_l2', sa.BigInteger(), nullable=True))
    op.create_index('idx_graph_embed_community_l0', 'graph_embeddings', ['community_id_l0'], unique=False)
    op.create_index('idx_graph_embed_community_l1', 'graph_embeddings', ['community_id_l1'], unique=False)
