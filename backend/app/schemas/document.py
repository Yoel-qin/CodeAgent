"""文档管理模块请求/响应 schema（Phase 1.5d，对齐 api接口清单 §文档管理 / 资源访问）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DocumentItem(BaseModel):
    file_id: int
    file_path: str
    title: str | None = None
    doc_type: str | None = None
    file_format: str | None = None
    total_pages: int | None = None
    total_tables: int | None = None
    total_chunks: int = 0
    parse_status: str | None = None
    ocr_required: bool | None = None
    file_size_bytes: int | None = None
    storage_path: str | None = None
    created_at: datetime | None = None


class DocumentDetail(DocumentItem):
    parse_engine: str | None = None
    parse_error: str | None = None
    last_commit: str | None = None
    updated_at: datetime | None = None


class DocumentListResponse(BaseModel):
    total: int
    items: list[DocumentItem]


class UploadResponse(BaseModel):
    file_id: int
    file_path: str
    file_format: str | None
    parse_status: str | None
    total_chunks: int
    storage_path: str | None
    message: str


class ParseProgressResponse(BaseModel):
    file_id: int
    parse_status: str | None
    parse_error: str | None
    total_pages: int | None
    total_tables: int | None
    total_chunks: int | None
    ocr_required: bool | None


class TableDataResponse(BaseModel):
    chunk_id: str
    table_data: dict | None = None
    table_html: str | None = None
    table_description: str | None = None
    table_total_rows: int | None = None
    table_total_cols: int | None = None


class TableListItem(BaseModel):
    chunk_id: str
    table_total_rows: int | None = None
    table_total_cols: int | None = None
    table_description: str | None = None
    is_table_fragment: bool | None = None


class TableListResponse(BaseModel):
    file_id: int
    total: int
    items: list[TableListItem]
