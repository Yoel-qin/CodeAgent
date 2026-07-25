"""关联关系表：chunk_relations / anchor_mappings（§10.5 / §10.9）。"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChunkRelation(Base):
    """切片关联关系（核心桥接表）。
    relation_type: DOC_TO_CODE / CODE_TO_DOC / CODE_CALLS_CODE / CODE_IMPLEMENTS /
                   CODE_EXTENDS / DOC_REFERENCES_DOC / CO_FILE / CO_MODULE
                   + §2.7.4: DOC_CONTAINS_IMAGE / DOC_CONTAINS_TABLE /
                             TABLE_FRAGMENT_OF / IMAGE_DESCRIBES_CODE
    """
    __tablename__ = "chunk_relations"

    relation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_chunk_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_chunk_id: Mapped[str] = mapped_column(String(128), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    anchor_key: Mapped[str | None] = mapped_column(String(512))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("source_chunk_id", "target_chunk_id", "relation_type", name="uk_relation"),
        Index("idx_relations_source", "source_chunk_id"),
        Index("idx_relations_target", "target_chunk_id"),
        Index("idx_relations_type", "relation_type"),
        Index("idx_relations_anchor", "anchor_key"),
        Index("idx_relations_active", "is_stale", postgresql_where="is_stale = false"),
    )


class AnchorMapping(Base):
    """代码锚点 ↔ 文档映射。"""
    __tablename__ = "anchor_mappings"

    mapping_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    anchor_key: Mapped[str] = mapped_column(String(512), nullable=False)
    code_chunk_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("code_chunks.chunk_id"))
    doc_chunk_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("doc_chunks.chunk_id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deactivated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    deactivated_by_commit: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("anchor_key", "code_chunk_id", "doc_chunk_id", name="uk_anchor_pair"),
        Index("idx_anchor_key", "anchor_key"),
        Index("idx_anchor_code", "code_chunk_id"),
        Index("idx_anchor_doc", "doc_chunk_id"),
        Index("idx_anchor_active", "is_active", postgresql_where="is_active = true"),
    )
