"""文档 ingest 管道：单文件全链路（解析→分段→PG/Milvus/ES/MinIO 幂等写入）。

幂等键：(repo, doc_name, file_hash, status=COMPLETED)。
同键存在 → 跳过（{"skipped": True}）。

外部依赖（Milvus/ES/MinIO/embedding）全部软失败——
不可用时 sections 照落 PG，日志提示，embedded 数为实际成功数。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.clients.embedding_client import embed_texts
from app.clients.es_client import bulk_index_sections, get_es
from app.clients.milvus_client import get_client, upsert_sections
from app.clients.minio_client import upload_original
from app.db.models.doc import DocSection, Document, MediaChunk
from app.pipeline.chunking.doc_chunker import chunk_doc_elements
from app.pipeline.chunking.doc_sections import build_doc_rows
from app.pipeline.parsing.router import doc_format_for, parse_doc

logger = logging.getLogger(__name__)


def _esc(v: str) -> str:
    """转义字符串用于 Milvus filter expr（防注入）。"""
    return v.replace("\\", "\\\\").replace('"', '\\"')


def ingest_doc_file(
    session: Session,
    *,
    repo: str,
    file_path: Path,
    data: bytes,
    reindex: bool = False,
) -> dict:
    """单文件全管道：hash→幂等跳过→parse→sections/media 组装→
    PG 事务（Document upsert + 删旧 sections + 插新）→
    MinIO（软失败）→ embed（空返则只标 embedding_synced=False）→
    Milvus/ES upsert（删旧 by doc_name 再插，各自 try/except）→
    返回统计 dict。

    PG 操作不在此函数 commit——由调用方控制事务边界。
    """
    file_hash = hashlib.sha256(data).hexdigest()
    doc_name = str(file_path)
    ext = file_path.suffix
    doc_type = doc_format_for(ext) or "unknown"

    # ---- 幂等跳过 ----
    if not reindex:
        existing = (
            session.query(Document)
            .filter_by(repo=repo, doc_name=doc_name, file_hash=file_hash, status="COMPLETED")
            .first()
        )
        if existing is not None:
            logger.debug("skip (idempotent): %s/%s", repo, doc_name)
            return {
                "doc_name": doc_name, "skipped": True,
                "sections": 0, "embedded": 0, "media": 0, "status": "COMPLETED",
            }

    # ---- 解析 ----
    try:
        elements, meta = parse_doc(data, ext, str(file_path))
    except Exception as exc:
        _upsert_document(
            session, repo=repo, doc_name=doc_name, source_path=str(file_path),
            doc_type=doc_type, file_hash=file_hash, status="FAILED",
            parse_meta={"error": str(exc)},
        )
        session.flush()
        return {
            "doc_name": doc_name, "skipped": False,
            "sections": 0, "embedded": 0, "media": 0, "status": "FAILED",
        }

    # ---- 分段 ----
    specs = chunk_doc_elements(elements, file_path=str(file_path), file_hash=file_hash)
    section_rows, _ = build_doc_rows(specs, document_id=0, repo=repo)

    # 图片段：直接从 DocElement 采集 IMAGE 元素
    media_count = sum(1 for el in elements if el.type == "IMAGE")

    # ---- PG 事务：Document upsert + 删旧 + 插新 ----
    doc = (
        session.query(Document)
        .filter_by(repo=repo, doc_name=doc_name)
        .first()
    )
    if doc is not None:
        doc.file_hash = file_hash
        doc.doc_type = doc_type
        doc.status = meta.parse_status
        doc.parse_meta = _meta_to_dict(meta)
        session.query(DocSection).filter_by(document_id=doc.id).delete()
        session.query(MediaChunk).filter_by(document_id=doc.id).delete()
    else:
        doc = Document(
            repo=repo, doc_name=doc_name[:512], source_path=str(file_path),
            doc_type=doc_type, file_hash=file_hash,
            status=meta.parse_status, parse_meta=_meta_to_dict(meta),
        )
        session.add(doc)
    session.flush()  # 获得 doc.id

    # Task 3 指针：anchor/title 截断到 DB 列宽 512
    for row in section_rows:
        row["document_id"] = doc.id
        row["anchor"] = row["anchor"][:512]
        row["title"] = row["title"][:512]

    # 插入 sections
    for row in section_rows:
        session.add(DocSection(**row))

    # 插入 media_chunks（IMAGE 元素，description 空串占位，OCR 不做）
    for el in elements:
        if el.type == "IMAGE":
            session.add(MediaChunk(
                document_id=doc.id, repo=repo, kind="image",
                description="", page=el.page_number, bbox=el.bbox,
            ))

    session.flush()

    # ---- MinIO（软失败）----
    try:
        minio_key = upload_original(repo, doc_name, data)
        if minio_key:
            doc.minio_key = minio_key
    except Exception:
        logger.debug("MinIO upload failed for %s/%s", repo, doc_name, exc_info=True)

    # ---- Embed（软失败 → 空列表）----
    texts = [r["content"] for r in section_rows]
    embeddings = embed_texts(texts)

    embedded_count = 0
    if embeddings and len(embeddings) == len(section_rows):
        for sec in session.query(DocSection).filter_by(document_id=doc.id).all():
            sec.embedding_synced = True
        embedded_count = len(embeddings)

    # ---- Milvus（Task 4 指针：自包 try/except）----
    # 删旧（独立 try，不阻塞后续 upsert）
    try:
        get_client().delete(
            collection_name="v2_doc_chunks",
            filter=f'doc_name == "{_esc(doc_name)}"',
        )
    except Exception:
        pass

    # 插新
    if embeddings and len(embeddings) == len(section_rows):
        try:
            sections = (
                session.query(DocSection)
                .filter_by(document_id=doc.id)
                .order_by(DocSection.order_index)
                .all()
            )
            milvus_rows = []
            for sec, emb in zip(sections, embeddings):
                milvus_rows.append({
                    "id": f"docsec_{sec.id}",
                    "embedding": emb,
                    "repo": repo,
                    "doc_name": doc_name,
                    "section": sec.anchor,
                    "title": sec.title,
                    "module": doc.module,
                    "page": sec.page or 0,
                })
            upsert_sections(milvus_rows)
        except Exception:
            logger.warning("Milvus upsert failed for %s/%s, PG only", repo, doc_name)

    # ---- ES（Task 4 指针：自包 try/except）----
    # 删旧（独立 try）
    try:
        get_es().delete_by_query(
            index="v2_doc_sections",
            body={"query": {"term": {"doc_name": doc_name}}},
            refresh=True,
        )
    except Exception:
        pass

    # 插新
    try:
        sections = (
            session.query(DocSection)
            .filter_by(document_id=doc.id)
            .order_by(DocSection.order_index)
            .all()
        )
        es_docs = []
        for sec in sections:
            es_docs.append({
                "section_id": f"docsec_{sec.id}",
                "repo": repo,
                "doc_name": doc_name,
                "title": sec.title,
                "anchor": sec.anchor,
                "module": doc.module,
                "content": sec.content,
            })
        bulk_index_sections(es_docs)
    except Exception:
        logger.warning("ES bulk_index failed for %s/%s, PG only", repo, doc_name)

    return {
        "doc_name": doc_name,
        "skipped": False,
        "sections": len(section_rows),
        "embedded": embedded_count,
        "media": media_count,
        "status": meta.parse_status,
    }


def ingest_doc_repo(
    session: Session,
    *,
    repo: str,
    docs_dir: Path,
    reindex: bool = False,
) -> dict:
    """遍历 docs_dir 下的支持格式文件，逐文件调用 ingest_doc_file。

    进度日志每 20 文件打一行。不 commit——由调用方控制事务边界。
    """
    supported = {".md", ".markdown", ".pdf", ".docx", ".txt"}
    files = sorted(
        f for f in docs_dir.rglob("*")
        if f.suffix.lower() in supported and f.is_file()
    )

    stats = {
        "total": 0, "skipped": 0, "sections": 0,
        "embedded": 0, "media": 0, "failed": 0,
    }

    for i, f in enumerate(files):
        if (i + 1) % 20 == 0:
            logger.info(
                "Ingest progress: %d/%d files for repo=%s",
                i + 1, len(files), repo,
            )
        data = f.read_bytes()
        try:
            res = ingest_doc_file(
                session, repo=repo,
                file_path=f.relative_to(docs_dir),
                data=data, reindex=reindex,
            )
            stats["total"] += 1
            if res.get("skipped"):
                stats["skipped"] += 1
            else:
                stats["sections"] += res.get("sections", 0)
                stats["embedded"] += res.get("embedded", 0)
                stats["media"] += res.get("media", 0)
                if res.get("status") == "FAILED":
                    stats["failed"] += 1
        except Exception:
            logger.exception("Failed to ingest %s", f)
            stats["total"] += 1
            stats["failed"] += 1

    logger.info("Ingest done for repo=%s: %s", repo, stats)
    return stats


def _upsert_document(
    session: Session, *, repo: str, doc_name: str, source_path: str,
    doc_type: str, file_hash: str, status: str, parse_meta: dict,
) -> Document:
    """查找或创建 Document 行（不 commit）。"""
    doc = session.query(Document).filter_by(repo=repo, doc_name=doc_name).first()
    if doc is not None:
        doc.source_path = source_path
        doc.doc_type = doc_type
        doc.file_hash = file_hash
        doc.status = status
        doc.parse_meta = parse_meta
    else:
        doc = Document(
            repo=repo, doc_name=doc_name[:512], source_path=source_path,
            doc_type=doc_type, file_hash=file_hash,
            status=status, parse_meta=parse_meta,
        )
        session.add(doc)
    return doc


def _meta_to_dict(meta) -> dict:
    """ParseMeta → 可序列化 dict。"""
    return {k: v for k, v in meta.__dict__.items() if not k.startswith("_")}
