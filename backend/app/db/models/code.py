"""代码相关表：code_files / code_chunks / call_graph（后端设计 §10）。"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CodeFile(Base):
    __tablename__ = "code_files"

    file_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    package_name: Mapped[str | None] = mapped_column(String(256))
    module_name: Mapped[str | None] = mapped_column(String(128))
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    total_lines: Mapped[int | None] = mapped_column(Integer)
    last_commit: Mapped[str | None] = mapped_column(String(40))
    last_modified: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_code_files_module", "module_name"),
        Index("idx_code_files_package", "package_name"),
    )


class CodeChunk(Base):
    """代码切片（核心元数据）。对齐 §10.2 DDL。"""
    __tablename__ = "code_chunks"

    chunk_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("code_files.file_id"), nullable=False)

    # 代码定位
    chunk_type: Mapped[str] = mapped_column(String(32), nullable=False)
    class_name: Mapped[str | None] = mapped_column(String(256))
    method_name: Mapped[str | None] = mapped_column(String(256))
    method_signature: Mapped[str | None] = mapped_column(String(512))
    access_modifier: Mapped[str | None] = mapped_column(String(32))
    return_type: Mapped[str | None] = mapped_column(String(256))
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)

    # 语义内容
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    javadoc: Mapped[str | None] = mapped_column(Text)
    inline_comments: Mapped[dict] = mapped_column(JSONB, default=list)
    annotations: Mapped[dict] = mapped_column(JSONB, default=list)

    # 代码结构
    implements_interface: Mapped[str | None] = mapped_column(String(256))
    extends_class: Mapped[str | None] = mapped_column(String(256))
    type_parameters: Mapped[dict] = mapped_column(JSONB, default=list)

    # 关联信息
    linked_doc_ids: Mapped[dict] = mapped_column(JSONB, default=list)
    code_anchor_key: Mapped[str | None] = mapped_column(String(512))

    # 版本控制
    git_commit_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    git_commit_time: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at_commit: Mapped[str | None] = mapped_column(String(40))
    deleted_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))

    # 检索辅助
    keywords: Mapped[dict] = mapped_column(JSONB, default=list)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding_synced: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_code_chunks_file", "file_id"),
        Index("idx_code_chunks_class", "class_name"),
        Index("idx_code_chunks_method", "method_name"),
        Index("idx_code_chunks_type", "chunk_type"),
        Index("idx_code_chunks_anchor", "code_anchor_key"),
        Index("idx_code_chunks_hash", "content_hash"),
        Index("idx_code_chunks_commit", "git_commit_hash"),
        Index("idx_code_chunks_keywords", "keywords", postgresql_using="gin"),
        # 仅未删除的部分索引（高频过滤）
        Index("idx_code_chunks_active", "is_deleted", postgresql_where="is_deleted = false"),
    )


class CallGraph(Base):
    """代码调用图边。"""
    __tablename__ = "call_graph"

    edge_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    caller_chunk_id: Mapped[str] = mapped_column(String(128), ForeignKey("code_chunks.chunk_id"), nullable=False)
    callee_chunk_id: Mapped[str] = mapped_column(String(128), ForeignKey("code_chunks.chunk_id"), nullable=False)
    call_expression: Mapped[str | None] = mapped_column(String(512))
    call_line: Mapped[int | None] = mapped_column(Integer)
    is_recursive: Mapped[bool] = mapped_column(Boolean, default=False)
    git_commit_hash: Mapped[str | None] = mapped_column(String(40))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("caller_chunk_id", "callee_chunk_id", "call_line", name="uk_call_edge"),
        Index("idx_call_graph_caller", "caller_chunk_id"),
        Index("idx_call_graph_callee", "callee_chunk_id"),
    )
