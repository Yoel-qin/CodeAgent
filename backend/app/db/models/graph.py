"""图结构特征：graph_embeddings（§10.10）。

图向量列已于 2026-07-27 移除（见迁移 a3f1c08e9b42）；GraphRAG 社区摘要（社区检测 / 社区表 /
社区列）已于 2026-07-29 整体弃用并删除（见迁移 drop_graphrag_community_tables）。
graph_embeddings 现仅保留 pagerank/degree/betweenness 等通用结构特征，供将来 LTR / 重排复用。
"""
from __future__ import annotations

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GraphEmbedding(Base):
    """图结构特征（pagerank/degree/betweenness）；图向量列与社区列均已移除。"""
    __tablename__ = "graph_embeddings"

    chunk_id: Mapped[str] = mapped_column(String(128), ForeignKey("code_chunks.chunk_id"), primary_key=True)
    node_degree: Mapped[int] = mapped_column(Integer, default=0)
    in_degree: Mapped[int] = mapped_column(Integer, default=0)
    out_degree: Mapped[int] = mapped_column(Integer, default=0)
    pagerank: Mapped[float] = mapped_column(Float, default=0.0)
    betweenness: Mapped[float] = mapped_column(Float, default=0.0)
    model_version: Mapped[str | None] = mapped_column(String(64))
    computed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_graph_embed_pagerank", "pagerank"),)
