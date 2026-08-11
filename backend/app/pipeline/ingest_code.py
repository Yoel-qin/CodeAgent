"""代码入库编排：解析 → 切片 → upsert code_files/code_chunks（PG，同步）。

写入顺序遵循"PG 先写（事务）"的一致性策略（设计 §9.3）；ES 全文索引与 Milvus 向量
统一收敛到 :mod:`app.pipeline.indexing`（去重 + 批处理 + 失败置 ``embedding_synced=False``），
瞬时不可用导致的未同步 chunk 由 ``indexing.resync_pending_embeddings`` 补偿重试。

提供同步实现用于离线脚本与 CLI；API/Celery 路径后续补 async 版本。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import CodeChunk, CodeFile
from app.db.references import clear_code_chunk_refs
from app.pipeline import indexing
from app.pipeline.chunking.code_chunker import chunk_code_file
from app.pipeline.parsing.code_parser import parse_java


def _file_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def upsert_code_file(session: Session, *, file_path: str, package: str | None,
                     module_name: str | None, total_lines: int, source: str,
                     commit_hash: str) -> CodeFile:
    """按 file_path upsert code_files，返回 ORM 对象。"""
    fhash = _file_hash(source)
    stmt = select(CodeFile).where(CodeFile.file_path == file_path)
    cf = session.execute(stmt).scalar_one_or_none()
    if cf is None:
        cf = CodeFile(file_path=file_path, package_name=package, module_name=module_name,
                      file_hash=fhash, total_lines=total_lines, last_commit=commit_hash)
        session.add(cf)
    else:
        cf.package_name = package
        cf.module_name = module_name
        cf.file_hash = fhash
        cf.total_lines = total_lines
        cf.last_commit = commit_hash
        cf.is_deleted = False
    session.flush()
    return cf


def _to_orm(spec, file_id: int) -> CodeChunk:
    return CodeChunk(
        chunk_id=spec.chunk_id,
        file_id=file_id,
        chunk_type=spec.chunk_type,
        class_name=spec.class_name,
        method_name=spec.method_name,
        method_signature=spec.method_signature,
        access_modifier=spec.access_modifier,
        return_type=spec.return_type,
        start_line=spec.start_line,
        end_line=spec.end_line,
        content=spec.content,
        content_hash=spec.content_hash,
        javadoc=spec.javadoc,
        inline_comments=spec.inline_comments,
        annotations=spec.annotations,
        implements_interface=spec.implements_interface,
        extends_class=spec.extends_class,
        type_parameters=spec.type_parameters,
        code_anchor_key=spec.code_anchor_key,
        git_commit_hash=spec.git_commit_hash,
        keywords=spec.keywords,
        token_count=spec.token_count,
        embedding_synced=False,
    )


def replace_chunks(session: Session, file_id: int, specs: list) -> int:
    """删除该文件旧 chunk（含被引用的外键行），写入新 chunk；返回写入数。"""
    clear_code_chunk_refs(session, file_id)
    session.execute(delete(CodeChunk).where(CodeChunk.file_id == file_id))
    for spec in specs:
        session.add(_to_orm(spec, file_id))
    session.flush()
    return len(specs)


def ingest_java_source(session: Session, *, source: str, file_path: str,
                       commit_hash: str = "UNKNOWN", module_name: str | None = None,
                       small_file_lines: int | None = None) -> dict:
    """编排：解析 → 切片 → 入库。返回统计。"""
    pf = parse_java(source, file_path, module_name=module_name, commit_hash=commit_hash)
    cf = upsert_code_file(
        session, file_path=file_path, package=pf.package, module_name=pf.module_name,
        total_lines=pf.total_lines, source=source, commit_hash=commit_hash,
    )
    kwargs = {} if small_file_lines is None else {"small_file_lines": small_file_lines}
    specs = chunk_code_file(pf, commit_hash=commit_hash, **kwargs)
    n = replace_chunks(session, cf.file_id, specs)

    # 同步 ES 全文索引（路径 B；index_chunks_safe 已自吞错误，失败不阻断 PG 写入）
    try:
        indexing.index_chunks_to_es(file_path, [{
            "chunk_id": s.chunk_id, "kind": "code", "content": s.content,
            "keywords": s.keywords, "class_name": s.class_name,
            "method_name": s.method_name, "heading_path": [], "file_path": file_path,
        } for s in specs])
    except Exception:
        pass

    # 向量化入 Milvus（路径 A；嵌入未启用/编码器不可用则跳过，embedding_synced 留 False，
    # 由 indexing.resync_pending_embeddings 补偿重试）。保留外层 try/except：UPDATE 抛错不得破坏 PG insert。
    try:
        strat = settings.embedding_strategy
        if indexing._embed_enabled_for(strat, "code"):
            rows = [{"chunk_id": s.chunk_id, "text": indexing.embed_text_for("code", s)}
                    for s in specs]
            if indexing.index_chunks_to_milvus(strat, "code", rows):
                session.execute(
                    update(CodeChunk)
                    .where(CodeChunk.chunk_id.in_([s.chunk_id for s in specs]))
                    .values(embedding_synced=True)
                )
    except Exception:
        pass

    # M25：dual 模式额外把代码用 BGE-M3 嵌入写镜像索引 code_vectors_bge（让多语言 BGE-M3 也能检索代码，
    # 修 CodeBERT 对中文 NL 召回弱）。best-effort、独立 try/except，**永不翻 embedding_synced**
    # （那是主编码器的标志，与镜像无关；镜像缺失由 scripts/reindex_code_bge.py 补）。
    try:
        strat = settings.embedding_strategy
        if indexing._embed_enabled_for(strat, "code_bge"):
            bge_rows = [{"chunk_id": s.chunk_id, "text": indexing.embed_text_for("code", s)}
                        for s in specs]
            indexing.index_chunks_to_milvus(strat, "code_bge", bge_rows)
    except Exception:
        pass

    return {
        "file_path": file_path,
        "file_id": cf.file_id,
        "classes": len(pf.classes),
        "chunks": n,
        "method_chunks": sum(1 for s in specs if s.chunk_type == "method"),
    }


def ingest_java_file(session: Session, path: str | Path, *, commit_hash: str = "UNKNOWN",
                     repo_root: str | Path | None = None, module_name: str | None = None,
                     small_file_lines: int | None = None) -> dict:
    """读取并入库单个 .java 文件。file_path 用相对 repo_root 的路径。"""
    p = Path(path)
    source = p.read_text(encoding="utf-8", errors="replace")
    rel = str(p.relative_to(repo_root)).replace("\\", "/") if repo_root else p.name
    return ingest_java_source(session, source=source, file_path=rel,
                              commit_hash=commit_hash, module_name=module_name,
                              small_file_lines=small_file_lines)
