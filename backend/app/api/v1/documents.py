"""文档管理模块路由（Phase 1.5d §文档管理）：上传 / 列表 / 详情 / 解析进度 / 删除。

写路径经 ``asyncio.to_thread`` 调同步 document_service（位置参数包装，规避 to_thread
不能传 keyword-only 的坑，CLAUDE.md）。上传原件入 MinIO，复用 ingest_doc_bytes 解析入库。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.pipeline.parsing.router import DOC_FORMAT_EXTS
from app.schemas.document import (
    DocumentDetail,
    DocumentItem,
    DocumentListResponse,
    ParseProgressResponse,
    TableListItem,
    TableListResponse,
    UploadResponse,
)
from app.services import document_service

router = APIRouter(prefix="/documents", tags=["documents"])

_ALLOW_EXTS = set(DOC_FORMAT_EXTS.keys())


@router.post("/upload", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...), doc_type: str | None = Query(None)):
    """上传单个文档（md/pdf/docx/txt/...）→ MinIO → 解析入库。"""
    filename = file.filename or "upload"
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in _ALLOW_EXTS:
        raise HTTPException(
            415, f"不支持的文档格式 {ext or '(无扩展名)'}；支持 {sorted(_ALLOW_EXTS)}")
    data = await file.read()
    try:
        result = await asyncio.to_thread(
            document_service.upload_document, data, filename, doc_type,
            file.content_type or "application/octet-stream")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"上传失败：{type(e).__name__}: {e}") from e
    return result


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    file_format: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    total, items = await asyncio.to_thread(
        document_service.list_documents, page, page_size, file_format)
    return DocumentListResponse(total=total, items=[DocumentItem(**i) for i in items])


@router.get("/{file_id}", response_model=DocumentDetail)
async def get_document(file_id: int):
    item = await asyncio.to_thread(document_service.get_document, file_id)
    if item is None:
        raise HTTPException(404, "文档不存在")
    return item


@router.get("/{file_id}/parse-progress", response_model=ParseProgressResponse)
async def parse_progress(file_id: int):
    item = await asyncio.to_thread(document_service.parse_progress, file_id)
    if item is None:
        raise HTTPException(404, "文档不存在")
    return item


@router.get("/{file_id}/tables", response_model=TableListResponse)
async def list_tables(file_id: int):
    """列出某文档的表格 chunk（供前端表格预览）。"""
    rows = await asyncio.to_thread(document_service.tables_for_document, file_id)
    return TableListResponse(
        file_id=file_id, total=len(rows),
        items=[TableListItem(**r) for r in rows])


@router.delete("/{file_id}")
async def delete_document(file_id: int):
    ok = await asyncio.to_thread(document_service.delete_document, file_id)
    if not ok:
        raise HTTPException(404, "文档不存在或已删除")
    return {"file_id": file_id, "deleted": True}
