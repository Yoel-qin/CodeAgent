"""文档相关表：doc_files / doc_chunks（含 PDF/Word 扩展 §2.7）/ doc_resources。"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocFile(Base):
    """文档文件（含 PDF/Word 扩展字段 §2.7.1）。"""
    __tablename__ = "doc_files"

    file_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(String(512))
    doc_type: Mapped[str | None] = mapped_column(String(64))
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    last_commit: Mapped[str | None] = mapped_column(String(40))
    last_modified: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    # PDF/Word 扩展 §2.7.1
    file_format: Mapped[str | None] = mapped_column(String(16))  # markdown/pdf/docx/doc/html/txt
    total_pages: Mapped[int | None] = mapped_column(Integer)
    total_images: Mapped[int | None] = mapped_column(Integer)
    total_tables: Mapped[int | None] = mapped_column(Integer)
    parse_engine: Mapped[str | None] = mapped_column(String(64))
    parse_status: Mapped[str | None] = mapped_column(String(32))  # PENDING/PARSING/COMPLETED/FAILED/PARTIAL
    parse_error: Mapped[str | None] = mapped_column(Text)
    ocr_required: Mapped[bool | None] = mapped_column(Boolean)
    storage_path: Mapped[str | None] = mapped_column(String(512))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    uploaded_by: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_doc_files_format", "file_format"),)


class DocChunk(Base):
    """文档切片（含图片/表格扩展 §2.7.2）。"""
    __tablename__ = "doc_chunks"

    chunk_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("doc_files.file_id"), nullable=False)

    # 文档定位
    heading_path: Mapped[dict] = mapped_column(JSONB, default=list)
    heading_level: Mapped[int | None] = mapped_column(SmallInteger)
    section_order: Mapped[int | None] = mapped_column(Integer)

    # 内容
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)

    # 关联信息
    code_anchors: Mapped[dict] = mapped_column(JSONB, default=list)
    linked_code_ids: Mapped[dict] = mapped_column(JSONB, default=list)
    inline_code_blocks: Mapped[dict] = mapped_column(JSONB, default=list)
    stale_anchors: Mapped[dict] = mapped_column(JSONB, default=list)

    # 版本控制
    git_commit_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    git_commit_time: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at_commit: Mapped[str | None] = mapped_column(String(40))

    # 检索辅助
    keywords: Mapped[dict] = mapped_column(JSONB, default=list)
    embedding_synced: Mapped[bool] = mapped_column(Boolean, default=False)

    # PDF/Word 图片/表格扩展 §2.7.2
    chunk_content_type: Mapped[str | None] = mapped_column(String(32))  # text/image/table/table_fragment/figure_group/code_block
    page_number: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict | None] = mapped_column(JSONB)
    # 图片
    image_url: Mapped[str | None] = mapped_column(String(512))
    image_thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    image_description: Mapped[str | None] = mapped_column(Text)
    image_width: Mapped[int | None] = mapped_column(Integer)
    image_height: Mapped[int | None] = mapped_column(Integer)
    image_caption: Mapped[str | None] = mapped_column(String(512))
    context_before: Mapped[str | None] = mapped_column(Text)
    context_after: Mapped[str | None] = mapped_column(Text)
    # 表格
    table_data: Mapped[dict | None] = mapped_column(JSONB)
    table_html: Mapped[str | None] = mapped_column(Text)
    table_description: Mapped[str | None] = mapped_column(Text)
    table_total_rows: Mapped[int | None] = mapped_column(Integer)
    table_total_cols: Mapped[int | None] = mapped_column(Integer)
    is_table_fragment: Mapped[bool | None] = mapped_column(Boolean)
    table_fragment_index: Mapped[int | None] = mapped_column(Integer)
    parent_table_chunk_id: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_doc_chunks_file", "file_id"),
        Index("idx_doc_chunks_heading", "heading_path", postgresql_using="gin"),
        Index("idx_doc_chunks_anchors", "code_anchors", postgresql_using="gin"),
        Index("idx_doc_chunks_hash", "content_hash"),
        Index("idx_doc_chunks_active", "is_deleted", postgresql_where="is_deleted = false"),
        Index("idx_doc_chunks_content_type", "chunk_content_type"),
    )


class DocResource(Base):
    """文档资源表（图片/表格原件）§2.7.3。"""
    __tablename__ = "doc_resources"

    resource_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("doc_files.file_id"))
    resource_type: Mapped[str | None] = mapped_column(String(32))  # image/table
    storage_path: Mapped[str | None] = mapped_column(String(512))
    thumbnail_path: Mapped[str | None] = mapped_column(String(512))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    chunk_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("doc_chunks.chunk_id"))
    description: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
