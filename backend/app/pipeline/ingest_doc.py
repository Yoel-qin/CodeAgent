"""文档入库编排（Phase 1.5a 多格式）：按扩展名路由解析（markdown/pdf/docx/txt）→ 切片
→ upsert doc_files/doc_chunks（PG，同步）→ ES 全文 + Milvus 向量。

解析统一走 :mod:`app.pipeline.parsing.router` 的 ``parse_doc``；``ParseMeta`` 填
``doc_files`` 的 file_format/parse_engine/parse_status/ocr_required/total_pages 等列
（替换 Phase 1 硬编码的 markdown）。ES/Milvus 索引只读 ``content``，格式无关，路径不变。
瞬时不可用导致的未同步 chunk 由 ``indexing.resync_pending_embeddings`` 补偿重试。

``ingest_markdown_file``/``ingest_markdown_source`` 保留为薄包装（供 sync_incremental 等
既有调用方），统一走 ``parse_doc``——单一解析路径，无分叉。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DocChunk, DocFile, DocResource
from app.db.references import clear_doc_chunk_refs
from app.pipeline import images, indexing
from app.pipeline.chunking.doc_chunker import chunk_doc_elements
from app.pipeline.parsing.doc_element import DocElement, ParseMeta
from app.pipeline.parsing.router import parse_doc
from app.pipeline.parsing.txt_parser import decode_text


def _file_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _derive_title(elements: list[DocElement], file_path: str) -> str:
    """首个 HEADING 文本作标题，否则取文件名 stem（多格式通用）。"""
    for el in elements:
        if el.type == "HEADING" and el.content.strip():
            return el.content.strip()
    return Path(file_path).stem


def upsert_doc_file(session: Session, *, file_path: str, file_hash: str, title: str,
                    commit_hash: str, doc_type: str | None, meta: ParseMeta,
                    file_size_bytes: int | None = None,
                    storage_path: str | None = None) -> DocFile:
    """按 ParseMeta 写 doc_files（含 file_format/parse_engine/parse_status 等）。"""
    cf = session.execute(select(DocFile).where(DocFile.file_path == file_path)).scalar_one_or_none()
    vals = dict(
        title=title, doc_type=doc_type, file_hash=file_hash,
        file_format=meta.file_format, parse_engine=meta.parse_engine,
        parse_status=meta.parse_status, parse_error=meta.parse_error,
        ocr_required=meta.ocr_required, total_pages=meta.total_pages,
        total_tables=meta.total_tables, file_size_bytes=file_size_bytes,
        storage_path=storage_path, last_commit=commit_hash, is_deleted=False,
    )
    if cf is None:
        cf = DocFile(file_path=file_path, **vals)
        session.add(cf)
    else:
        for k, v in vals.items():
            setattr(cf, k, v)
    session.flush()
    return cf


def _to_orm(spec, file_id: int) -> DocChunk:
    return DocChunk(
        chunk_id=spec.chunk_id,
        file_id=file_id,
        heading_path=spec.heading_path,
        heading_level=spec.heading_level,
        section_order=spec.section_order,
        content=spec.content,
        content_hash=spec.content_hash,
        token_count=spec.token_count,
        code_anchors=spec.code_anchors,
        keywords=spec.keywords,
        git_commit_hash=spec.git_commit_hash,
        page_number=spec.page_number,
        chunk_content_type=spec.chunk_content_type,
        table_data=spec.table_data,
        table_html=spec.table_html,
        table_description=spec.table_description,
        table_total_rows=spec.table_total_rows,
        table_total_cols=spec.table_total_cols,
        is_table_fragment=spec.is_table_fragment,
        table_fragment_index=spec.table_fragment_index,
        parent_table_chunk_id=spec.parent_table_chunk_id,
        image_url=spec.image_url,
        image_thumbnail_url=spec.image_thumbnail_url,
        image_description=spec.image_description,
        image_width=spec.image_width,
        image_height=spec.image_height,
        image_caption=spec.image_caption,
        context_before=spec.context_before,
        context_after=spec.context_after,
        embedding_synced=False,
    )


def _index_external(session: Session, specs, file_path: str) -> None:
    """同步 ES 全文 + Milvus 向量（best-effort，自吞；与 Phase 1 行为一致）。"""
    try:
        indexing.index_chunks_to_es(file_path, [{
            "chunk_id": s.chunk_id, "kind": "doc", "content": s.content,
            "keywords": s.keywords, "class_name": None, "method_name": None,
            "heading_path": s.heading_path, "file_path": file_path,
        } for s in specs])
    except Exception:
        pass
    try:
        strat = settings.embedding_strategy
        if indexing._embed_enabled_for(strat, "doc"):
            rows = [{"chunk_id": s.chunk_id, "text": indexing.embed_text_for("doc", s)}
                    for s in specs]
            if indexing.index_chunks_to_milvus(strat, "doc", rows):
                session.execute(
                    update(DocChunk)
                    .where(DocChunk.chunk_id.in_([s.chunk_id for s in specs]))
                    .values(embedding_synced=True)
                )
    except Exception:
        pass


def _ingest_doc_elements(session: Session, *, raw: bytes, elements: list[DocElement],
                         meta: ParseMeta, file_path: str, commit_hash: str,
                         doc_type: str | None, storage_path: str | None = None) -> dict:
    """解析后的统一入库：upsert file → chunk → 写库 → ES/Milvus。

    hash 基准：markdown/txt/html 用解码文本（clean UTF-8 与既有 chunk_id 一致），
    其余格式用原始字节。解析 FAILED（如 legacy .doc）仍记 DocFile 行，跳过切片。
    """
    if meta.parse_status == "FAILED":
        cf = upsert_doc_file(session, file_path=file_path, file_hash=_file_hash_bytes(raw),
                             title=Path(file_path).stem, commit_hash=commit_hash,
                             doc_type=doc_type, meta=meta, file_size_bytes=len(raw),
                             storage_path=storage_path)
        session.flush()
        return {"file_path": file_path, "file_id": cf.file_id, "chunks": 0, "anchors": 0,
                "parse_status": meta.parse_status, "parse_error": meta.parse_error}

    if meta.file_format in ("markdown", "txt", "html"):
        fhash = _file_hash(decode_text(raw))
    else:
        fhash = _file_hash_bytes(raw)

    title = _derive_title(elements, file_path)
    cf = upsert_doc_file(session, file_path=file_path, file_hash=fhash, title=title,
                         commit_hash=commit_hash, doc_type=doc_type, meta=meta,
                         file_size_bytes=len(raw), storage_path=storage_path)
    specs = chunk_doc_elements(elements, file_path=file_path, file_hash=fhash, commit_hash=commit_hash)
    # 图片（Phase 1.5b）：过滤/去重/MinIO 原图+缩略图/OCR → image specs + doc_resource
    image_specs, image_resources = images.process_images(
        elements, file_path=file_path, file_hash=fhash, commit_hash=commit_hash)
    clear_doc_chunk_refs(session, cf.file_id)
    session.execute(delete(DocChunk).where(DocChunk.file_id == cf.file_id))
    for spec in specs:
        session.add(_to_orm(spec, cf.file_id))
    for spec in image_specs:
        session.add(_to_orm(spec, cf.file_id))
    for res in image_resources:
        session.add(DocResource(file_id=cf.file_id, **res))
    specs.extend(image_specs)
    cf.total_chunks = len(specs)
    session.flush()
    anchors = sum(len(s.code_anchors) for s in specs)

    _index_external(session, specs, file_path)
    return {"file_path": file_path, "file_id": cf.file_id, "chunks": len(specs), "anchors": anchors}


def ingest_doc_file(session: Session, path: str | Path, *, commit_hash: str = "UNKNOWN",
                    repo_root: str | Path | None = None, doc_type: str | None = None) -> dict:
    """读盘（按字节，二进制安全）→ 按扩展名路由解析 → 入库。多格式主入口。"""
    p = Path(path)
    raw = p.read_bytes()
    rel = str(p.relative_to(repo_root)).replace("\\", "/") if repo_root else p.name
    elements, meta = parse_doc(raw, p.suffix.lower(), rel)
    return _ingest_doc_elements(session, raw=raw, elements=elements, meta=meta,
                                file_path=rel, commit_hash=commit_hash, doc_type=doc_type)


def ingest_markdown_file(session: Session, path: str | Path, *, commit_hash: str = "UNKNOWN",
                         repo_root: str | Path | None = None, doc_type: str | None = None) -> dict:
    """markdown 文件入库——薄包装，统一走 ingest_doc_file（按扩展名 .md 路由）。"""
    return ingest_doc_file(session, path, commit_hash=commit_hash,
                           repo_root=repo_root, doc_type=doc_type)


def ingest_markdown_source(session: Session, *, source: str, file_path: str,
                           commit_hash: str = "UNKNOWN", doc_type: str | None = None) -> dict:
    """内存 markdown 字符串入库（无磁盘文件场景）。source 编码为 utf-8 字节后走统一路径。"""
    raw = source.encode("utf-8")
    elements, meta = parse_doc(raw, ".md", file_path)
    return _ingest_doc_elements(session, raw=raw, elements=elements, meta=meta,
                                file_path=file_path, commit_hash=commit_hash, doc_type=doc_type)


def ingest_doc_bytes(session: Session, data: bytes, filename: str, *,
                     commit_hash: str = "UNKNOWN", doc_type: str | None = None,
                     storage_path: str | None = None) -> dict:
    """从内存字节入库（Phase 1.5d 文档上传路径，无磁盘文件）。

    按文件名扩展名路由解析；``storage_path`` 为 MinIO 对象 key（上传原件），写入 doc_files。
    """
    ext = Path(filename).suffix.lower()
    elements, meta = parse_doc(data, ext, filename)
    return _ingest_doc_elements(session, raw=data, elements=elements, meta=meta,
                                file_path=filename, commit_hash=commit_hash,
                                doc_type=doc_type, storage_path=storage_path)
