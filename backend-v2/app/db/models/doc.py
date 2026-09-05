"""文档三表 ORM：Document / DocSection / MediaChunk。

Spec 偏差记录（计划内决策）：
- spec §4.1 documents 无 repo 列——多仓库圈定必需，补之。
- 表格是可检索文本 → 进 doc_sections(kind='table') 而非 media_chunks。
- media_chunks v1 只收 image（OCR 不入 Plan 2）。

parse_meta / bbox 使用 SQLAlchemy 通用 JSON 类型，
PG 方言下自动映射为 JSONB（通过 .with_variant），
sqlite 冒烟测试使用原生 JSON（无需 PG 驱动）。
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_pg_jsonb = JSON().with_variant(JSONB, "postgresql")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo: Mapped[str] = mapped_column(String(256), index=True)
    doc_name: Mapped[str] = mapped_column(String(512))
    module: Mapped[str | None] = mapped_column(String(256))
    source_path: Mapped[str] = mapped_column(String(1024))
    minio_key: Mapped[str | None] = mapped_column(String(1024))
    doc_type: Mapped[str] = mapped_column(String(32))  # markdown/pdf/docx/xlsx/txt
    status: Mapped[str] = mapped_column(String(32))  # COMPLETED/PARTIAL/FAILED
    file_hash: Mapped[str] = mapped_column(String(64))
    parse_meta: Mapped[dict] = mapped_column(_pg_jsonb, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("repo", "doc_name"),  # naming convention → uk_documents_repo_doc_name
    )


class DocSection(Base):
    __tablename__ = "doc_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    repo: Mapped[str] = mapped_column(String(256), index=True)  # 冗余列，免 join 过滤
    anchor: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(512))
    level: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))  # text|table
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    order_index: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    embedding_synced: Mapped[bool] = mapped_column(default=False)


class MediaChunk(Base):
    __tablename__ = "media_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE")
    )
    repo: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))  # v1: image only
    description: Mapped[str] = mapped_column(Text, default="")  # v1 OCR 缺省空串
    minio_key: Mapped[str | None] = mapped_column(String(1024))
    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict | None] = mapped_column(_pg_jsonb, nullable=True)
