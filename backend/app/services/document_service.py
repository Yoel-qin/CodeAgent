"""文档上传/管理编排（Phase 1.5d）：上传原件入 MinIO → 复用 ingest_doc_bytes 解析入库
→ 列表 / 详情 / 解析进度 / 删除（软删 + MinIO + ES/Milvus 清理）→ 表格资源访问。

同步实现（与 ingest / sync 同步 Session 同驱动），经 ``asyncio.to_thread`` 供 API 调用；
故参数全为位置或关键字（无 keyword-only），规避 to_thread kwargs 坑（CLAUDE.md）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clients import minio_client
from app.db.models import DocChunk, DocFile, DocResource
from app.pipeline.ingest_doc import ingest_doc_bytes
from app.pipeline.sync_soft_delete import soft_delete_file
from app.services.sync_service import get_sync_engine

UPLOAD_COMMIT = "UPLOAD"
DELETE_COMMIT = "DELETE"


def _to_item(df: DocFile) -> dict:
    return {
        "file_id": df.file_id,
        "file_path": df.file_path,
        "title": df.title,
        "doc_type": df.doc_type,
        "file_format": df.file_format,
        "total_pages": df.total_pages,
        "total_tables": df.total_tables,
        "total_chunks": df.total_chunks or 0,
        "parse_status": df.parse_status,
        "ocr_required": df.ocr_required,
        "file_size_bytes": df.file_size_bytes,
        "storage_path": df.storage_path,
        "created_at": df.created_at,
    }


def upload_document(data: bytes, filename: str, doc_type: str | None = None,
                    content_type: str = "application/octet-stream") -> dict:
    """上传原件到 MinIO → 解析入库。MinIO put 先行；入库失败回滚并删孤儿对象。"""
    key = f"documents/{uuid.uuid4().hex}/{filename}"
    minio_client.put_bytes(key, data, content_type=content_type)
    engine = get_sync_engine()
    with Session(engine) as session:
        try:
            result = ingest_doc_bytes(session, data, filename, commit_hash=UPLOAD_COMMIT,
                                      doc_type=doc_type, storage_path=key)
            session.commit()
        except Exception:
            session.rollback()
            minio_client.remove_object(key)
            raise
    status = result.get("parse_status", "COMPLETED")
    msg = "上传并解析完成" if status != "FAILED" else f"上传成功但解析失败：{result.get('parse_error', '')}"
    return {
        "file_id": result["file_id"],
        "file_path": result["file_path"],
        "file_format": _format_of(filename),
        "parse_status": status,
        "total_chunks": result.get("chunks", 0),
        "storage_path": key,
        "message": msg,
    }


def list_documents(page: int = 1, page_size: int = 20,
                   file_format: str | None = None) -> tuple[int, list[dict]]:
    """列出已上传文档（storage_path 非空）。"""
    engine = get_sync_engine()
    with Session(engine) as session:
        base = DocFile.storage_path.is_not(None)
        q = select(DocFile).where(base, DocFile.is_deleted == False).order_by(DocFile.created_at.desc())  # noqa: E712
        cq = select(func.count()).select_from(DocFile).where(base, DocFile.is_deleted == False)  # noqa: E712
        if file_format:
            q, cq = q.where(DocFile.file_format == file_format), cq.where(DocFile.file_format == file_format)
        total = session.execute(cq).scalar_one()
        rows = session.execute(q.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return total, [_to_item(r) for r in rows]


def get_document(file_id: int) -> dict | None:
    engine = get_sync_engine()
    with Session(engine) as session:
        df = session.get(DocFile, file_id)
        if df is None or df.is_deleted:
            return None
        item = _to_item(df)
        item.update({
            "parse_engine": df.parse_engine,
            "parse_error": df.parse_error,
            "last_commit": df.last_commit,
            "updated_at": df.updated_at,
        })
    return item


def parse_progress(file_id: int) -> dict | None:
    engine = get_sync_engine()
    with Session(engine) as session:
        df = session.get(DocFile, file_id)
        if df is None:
            return None
    return {
        "file_id": df.file_id,
        "parse_status": df.parse_status,
        "parse_error": df.parse_error,
        "total_pages": df.total_pages,
        "total_tables": df.total_tables,
        "total_chunks": df.total_chunks,
        "ocr_required": df.ocr_required,
    }


def delete_document(file_id: int) -> bool:
    """软删文档：§6.4 软删级联（chunks/relations/Milvus/ES）+ 删 MinIO 原件 + 标记 doc_files。"""
    engine = get_sync_engine()
    with Session(engine) as session:
        df = session.get(DocFile, file_id)
        if df is None or df.is_deleted:
            return False
        storage_path = df.storage_path
        soft_delete_file(session, file_path=df.file_path, kind="doc", delete_commit=DELETE_COMMIT)
        df.is_deleted = True
        session.commit()
    if storage_path:
        minio_client.remove_object(storage_path)
    return True


def table_data_for_chunk(chunk_id: str) -> dict | None:
    """返回某 chunk 的结构化表格数据（仅 table/table_fragment chunk）。"""
    engine = get_sync_engine()
    with Session(engine) as session:
        c = session.get(DocChunk, chunk_id)
        if c is None or c.chunk_content_type not in ("table", "table_fragment"):
            return None
    return {
        "chunk_id": c.chunk_id,
        "table_data": c.table_data,
        "table_html": c.table_html,
        "table_description": c.table_description,
        "table_total_rows": c.table_total_rows,
        "table_total_cols": c.table_total_cols,
    }


def get_image_for_chunk(chunk_id: str, thumbnail: bool = False) -> tuple[bytes, str] | None:
    """按 chunk_id 取图片字节（Phase 1.5e）：查 doc_resources → MinIO 取原图/缩略图。
    返回 (bytes, mime_type)；无资源/取失败返回 None。"""
    engine = get_sync_engine()
    with Session(engine) as session:
        row = session.execute(
            select(DocResource).where(DocResource.chunk_id == chunk_id).limit(1)
        ).scalar_one_or_none()
    if row is None:
        return None
    key = row.thumbnail_path if (thumbnail and row.thumbnail_path) else row.storage_path
    if not key:
        return None
    data = minio_client.get_bytes(key)
    if data is None:
        return None
    return data, row.mime_type or "image/png"


def tables_for_document(file_id: int) -> list[dict]:
    """列出某文档的表格 chunk（供前端表格预览）。"""
    engine = get_sync_engine()
    with Session(engine) as session:
        rows = session.execute(
            select(DocChunk).where(
                DocChunk.file_id == file_id,
                DocChunk.chunk_content_type.in_(["table", "table_fragment"]),
                DocChunk.is_deleted == False,  # noqa: E712
            ).order_by(DocChunk.section_order)
        ).scalars().all()
    return [{
        "chunk_id": r.chunk_id,
        "table_total_rows": r.table_total_rows,
        "table_total_cols": r.table_total_cols,
        "table_description": r.table_description,
        "is_table_fragment": r.is_table_fragment,
    } for r in rows]


def _format_of(filename: str) -> str | None:
    from app.pipeline.parsing.router import doc_format_for
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
    return doc_format_for(ext)
