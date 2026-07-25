"""图嵌入与社区：graph_embeddings / graph_communities / node_community_mapping（§10.10-12）。"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GraphEmbedding(Base):
    """图嵌入向量 + 图结构特征（主存 Milvus，PG 备份/特征查询）。"""
    __tablename__ = "graph_embeddings"

    chunk_id: Mapped[str] = mapped_column(String(128), ForeignKey("code_chunks.chunk_id"), primary_key=True)
    graph_embedding: Mapped[bytes | None] = mapped_column(LargeBinary)  # BYTEA
    embedding_dim: Mapped[int] = mapped_column(Integer, default=256)
    node_degree: Mapped[int] = mapped_column(Integer, default=0)
    in_degree: Mapped[int] = mapped_column(Integer, default=0)
    out_degree: Mapped[int] = mapped_column(Integer, default=0)
    pagerank: Mapped[float] = mapped_column(Float, default=0.0)
    betweenness: Mapped[float] = mapped_column(Float, default=0.0)
    community_id_l0: Mapped[int | None] = mapped_column(BigInteger)
    community_id_l1: Mapped[int | None] = mapped_column(BigInteger)
    community_id_l2: Mapped[int | None] = mapped_column(BigInteger)
    model_version: Mapped[str | None] = mapped_column(String(64))
    computed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_graph_embed_pagerank", "pagerank"),
        Index("idx_graph_embed_community_l0", "community_id_l0"),
        Index("idx_graph_embed_community_l1", "community_id_l1"),
    )


class GraphCommunity(Base):
    """图社区（GraphRAG）。"""
    __tablename__ = "graph_communities"

    community_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, default=0)
    member_chunk_ids: Mapped[dict] = mapped_column(JSONB, default=list)
    summary_embedding_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at_commit: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_community_level", "level"),)


class NodeCommunityMapping(Base):
    """节点-社区归属。"""
    __tablename__ = "node_community_mapping"

    chunk_id: Mapped[str] = mapped_column(String(128), nullable=False, primary_key=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False, primary_key=True)
    community_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("graph_communities.community_id"), nullable=False)
    is_centroid: Mapped[bool] = mapped_column(Boolean, default=False)
    pagerank_in_community: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("idx_node_community", "chunk_id"),
        Index("idx_community_nodes", "community_id"),
    )
