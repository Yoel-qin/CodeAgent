"""文档入库编排：markdown 解析 → 切片 → upsert doc_files/doc_chunks（PG，同步）。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import DocChunk, DocFile
from app.db.references import clear_doc_chunk_refs
from app.pipeline.chunking.doc_chunker import chunk_doc_elements
from app.pipeline.parsing.markdown_parser import parse_markdown


def _file_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _derive_title(source: str, file_path: str) -> str:
    for line in source.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return Path(file_path).stem


def upsert_doc_file(session: Session, *, file_path: str, source: str, commit_hash: str,
                    doc_type: str | None = None) -> DocFile:
    fhash = _file_hash(source)
    title = _derive_title(source, file_path)
    cf = session.execute(select(DocFile).where(DocFile.file_path == file_path)).scalar_one_or_none()
    if cf is None:
        cf = DocFile(file_path=file_path, title=title, doc_type=doc_type, file_hash=fhash,
                     file_format="markdown", parse_engine="markdown", parse_status="COMPLETED",
                     last_commit=commit_hash)
        session.add(cf)
    else:
        cf.title, cf.doc_type, cf.file_hash = title, doc_type, fhash
        cf.file_format, cf.parse_engine, cf.parse_status = "markdown", "markdown", "COMPLETED"
        cf.last_commit, cf.is_deleted = commit_hash, False
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
        embedding_synced=False,
    )


def ingest_markdown_source(session: Session, *, source: str, file_path: str,
                           commit_hash: str = "UNKNOWN", doc_type: str | None = None) -> dict:
    elements = parse_markdown(source, file_path)
    fhash = _file_hash(source)
    cf = upsert_doc_file(session, file_path=file_path, source=source,
                         commit_hash=commit_hash, doc_type=doc_type)
    specs = chunk_doc_elements(elements, file_path=file_path, file_hash=fhash, commit_hash=commit_hash)
    clear_doc_chunk_refs(session, cf.file_id)
    session.execute(delete(DocChunk).where(DocChunk.file_id == cf.file_id))
    for spec in specs:
        session.add(_to_orm(spec, cf.file_id))
    cf.total_chunks = len(specs)
    session.flush()
    anchors = sum(len(s.code_anchors) for s in specs)

    # 同步 ES 全文索引（路径 B；失败不阻断）
    try:
        from app.clients import es_client
        es_client.index_chunks_safe(file_path, [{
            "chunk_id": s.chunk_id, "kind": "doc", "content": s.content,
            "keywords": s.keywords, "class_name": None, "method_name": None,
            "heading_path": s.heading_path, "file_path": file_path,
        } for s in specs])
    except Exception:
        pass

    # 向量化入 Milvus（路径 A；嵌入未启用则跳过）
    try:
        from app.clients import embedding_client, milvus_client
        if embedding_client.enabled():
            vecs = embedding_client.embed_texts_sync([s.content for s in specs])
            milvus_client.upsert_vectors([
                {"chunk_id": s.chunk_id, "embedding": vecs[i], "kind": "doc"}
                for i, s in enumerate(specs)
            ])
    except Exception:
        pass

    return {"file_path": file_path, "file_id": cf.file_id, "chunks": len(specs), "anchors": anchors}


def ingest_markdown_file(session: Session, path: str | Path, *, commit_hash: str = "UNKNOWN",
                         repo_root: str | Path | None = None, doc_type: str | None = None) -> dict:
    p = Path(path)
    source = p.read_text(encoding="utf-8", errors="replace")
    rel = str(p.relative_to(repo_root)).replace("\\", "/") if repo_root else p.name
    return ingest_markdown_source(session, source=source, file_path=rel,
                                  commit_hash=commit_hash, doc_type=doc_type)
