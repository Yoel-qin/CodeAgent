"""Worker D（Task 13）：file(文档扩展) 事件 → 文档 ingest / 删除。

A/M → :func:`ingest_doc_file`（幂等 hash skip 内建——同 (repo, doc_name, file_hash,
COMPLETED) 直接 ``{"skipped": True}``）；D → :func:`delete_doc`（PG 三表 +
Milvus/ES 清理，Plan 2 终审 I-1 的删除谓词函数化复用）。

非文档扩展 → ``{"skipped": True}``（runner 只把文档扩展路由进来，这里再挡一道
双保险）。文件缺失 / 读取失败 → :class:`WorkerError`（重试/死信）。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.pipeline.ingest_doc import delete_doc, ingest_doc_file
from app.pipeline.workers import WorkerError, repo_dir_of

# 与 ingest_doc_repo 支持集一致
DOC_EXTS = frozenset({".md", ".markdown", ".pdf", ".docx", ".txt"})
_MUTABLE_STATUS = ("A", "M", "D")


def process_doc_file(session: Session, *, repo: str, path: str, status: str) -> dict:
    """单个文档变更的 ingest/删除（不 commit——runner 控制每事件事务边界）。"""
    if Path(path).suffix.lower() not in DOC_EXTS:
        return {"skipped": True, "reason": f"unsupported doc ext: {path!r}"}
    if status not in _MUTABLE_STATUS:
        return {"skipped": True, "reason": f"unsupported status: {status!r}"}

    # doc_name 与 _ingest_doc_pg 同规：posix 归一 + 截 512
    doc_name = Path(path).as_posix()[:512]
    if status == "D":
        return {**delete_doc(session, repo=repo, doc_name=doc_name), "skipped": False}

    fp = repo_dir_of(repo) / path
    if not fp.is_file():
        raise WorkerError(f"文档文件不存在: {fp}")
    try:
        data = fp.read_bytes()
    except OSError as exc:
        raise WorkerError(f"文档读取失败: {fp}: {exc}") from exc
    return ingest_doc_file(session, repo=repo, file_path=Path(path), data=data)
