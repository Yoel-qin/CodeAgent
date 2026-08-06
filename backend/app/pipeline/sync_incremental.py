"""增量同步的 ADDED/MODIFIED 应用（设计 §13.2 步骤 3–4）。

对 git diff 检测到的「新增/修改」文件，**复用** ``ingest_code.ingest_java_file`` /
``ingest_doc.ingest_doc_file``（按扩展名路由解析，含 .md/.pdf/.docx/.txt），
仅在外层加两件事：

1. **Milvus 孤儿清理**：``replace_chunks`` 硬删了 PG 旧行，但 Milvus 里旧向量仍在——
   对比重入库前后的 ``chunk_id`` 集，差集（旧有新无）调 ``delete_chunks_from_milvus`` 清掉。
2. **变更分类 + change_history 收集**：code 按稳定锚点 ``code_anchor_key`` 做 per-chunk
   ADDED/MODIFIED/DELETED；doc 因 ``chunk_id`` 含 fileHash（文件一改全变）做**文件级** MODIFIED。

``change_history`` 行**不在此写**——先收集成 ``Change`` 列表返回，由 ``sync_service`` 交给
``sync_rollback.classify_rollbacks`` 做回滚重标记（ROLLBACK/RESTORED）后统一落库。
**不碰** ingest 热路径与 ``replace_chunks`` 硬删除语义。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CodeChunk, CodeFile, DocChunk, DocFile
from app.pipeline import indexing
from app.pipeline.ingest_code import ingest_java_file
from app.pipeline.ingest_doc import ingest_doc_file
from app.pipeline.sync_git import FileChange

_FILE_MODEL = {"code": CodeFile, "doc": DocFile}
_CHUNK_MODEL = {"code": CodeChunk, "doc": DocChunk}


@dataclass
class Change:
    """单个 chunk 的变更记录（change_history 一行的素材）。

    ``change_type`` 初值为 ADDED/MODIFIED/DELETED；``classify_rollbacks`` 可能改为
    ROLLBACK/RESTORED 并填 ``rollback_source_commit``。``file_path`` 用于回查/审计
    （change_history 表本身无该列，仅用于 change_details JSONB 与日志）。
    """

    chunk_id: str
    chunk_type: str
    change_type: str
    file_path: str
    old_content_hash: str | None = None
    new_content_hash: str | None = None
    rollback_source_commit: str | None = field(default=None)
    is_rollback_related: bool = field(default=False)


def _file_id(session: Session, kind: str, file_path: str) -> int | None:
    file_model = _FILE_MODEL[kind]
    return session.execute(
        select(file_model.file_id).where(file_model.file_path == file_path)
    ).scalar_one_or_none()


def _snapshot_code(session: Session, file_id: int) -> list[tuple[str, str, str | None]]:
    """重入库前该 code 文件的活跃 chunk：(chunk_id, content_hash, code_anchor_key)。"""
    return list(session.execute(
        select(CodeChunk.chunk_id, CodeChunk.content_hash, CodeChunk.code_anchor_key).where(
            CodeChunk.file_id == file_id, CodeChunk.is_deleted == False  # noqa: E712
        )
    ).all())


def _snapshot_ids(session: Session, kind: str, file_id: int) -> set[str]:
    """重入库前该文件的活跃 chunk_id 集合（doc 用；code 也用于孤儿清理并集）。"""
    model = _CHUNK_MODEL[kind]
    rows = session.execute(
        select(model.chunk_id).where(model.file_id == file_id, model.is_deleted == False)  # noqa: E712
    ).scalars().all()
    return set(rows)


def _current_code(session: Session, file_id: int) -> list[tuple[str, str, str | None]]:
    """重入库后该 code 文件的活跃 chunk（同 _snapshot_code，重入库后查）。"""
    return list(session.execute(
        select(CodeChunk.chunk_id, CodeChunk.content_hash, CodeChunk.code_anchor_key).where(
            CodeChunk.file_id == file_id, CodeChunk.is_deleted == False  # noqa: E712
        )
    ).all())


def apply_added_or_modified(
    session: Session, repo_path: str | Path, fc: FileChange, *, new_commit: str,
) -> tuple[dict, list[Change]]:
    """对单个 ADDED/MODIFIED 文件重入库并分类。返回 (统计, change 列表)。

    失败抛异常——由 ``run_sync`` 的 per-file try/except 捕获记入 task，不中断其它文件。
    """
    kind = fc.kind or "code"
    disk_path = Path(repo_path) / fc.file_path

    # 重入库前的活跃 chunk 快照
    old_file_id = _file_id(session, kind, fc.file_path)
    old_ids: set[str] = set()
    old_code: list[tuple[str, str, str | None]] = []
    if old_file_id is not None:
        old_ids = _snapshot_ids(session, kind, old_file_id)
        if kind == "code":
            old_code = _snapshot_code(session, old_file_id)

    # 复用既有 per-file 入库（读盘 → 解析 → 切片 → upsert → ES → Milvus）
    if kind == "code":
        ingest = ingest_java_file(session, disk_path, commit_hash=new_commit, repo_root=repo_path)
    else:
        ingest = ingest_doc_file(session, disk_path, commit_hash=new_commit, repo_root=repo_path)
    new_file_id = ingest["file_id"]

    # 重入库后的活跃 chunk
    new_ids = _snapshot_ids(session, kind, new_file_id)

    # Milvus 孤儿清理：旧有新无（replace_chunks 已硬删 PG 行，Milvus 旧向量仍在）
    orphans = old_ids - new_ids
    if orphans:
        try:
            indexing.delete_chunks_from_milvus(
                _settings_strategy(), kind, list(orphans))
        except Exception as e:
            logger.warning(f"[incremental] Milvus 孤儿清理失败 {fc.file_path}: {type(e).__name__}: {e}")

    changes: list[Change] = []
    if kind == "code":
        changes = _classify_code(fc.file_path, old_code, _current_code(session, new_file_id))
    else:
        # doc：文件级 MODIFIED/ADDED（chunk_id churn，不做 per-chunk）
        if new_ids:
            changes = [Change(
                chunk_id=sorted(new_ids)[0], chunk_type="doc",
                change_type=fc.change, file_path=fc.file_path,
            )]

    added = sum(1 for c in changes if c.change_type == "ADDED")
    modified = sum(1 for c in changes if c.change_type == "MODIFIED")
    deleted = sum(1 for c in changes if c.change_type == "DELETED")
    return ({"file_path": fc.file_path, "kind": kind, "added": added, "modified": modified,
             "deleted": deleted, "ingest": ingest}, changes)


def _classify_code(
    file_path: str,
    old: list[tuple[str, str, str | None]],
    new: list[tuple[str, str, str | None]],
) -> list[Change]:
    """按稳定锚点 ``code_anchor_key`` 把 code 文件的新旧 chunk 对齐分类。

    - 锚点相同、hash 相同 → 未变（跳过）
    - 锚点相同、hash 不同 → MODIFIED
    - 锚点不在旧集 → ADDED
    - 旧锚点不在新集 → DELETED（已被 replace_chunks 硬删 + 孤儿清理，仅记 change_history 审计）
    无锚点的 chunk（文件级/类级）退化为按 chunk_id 匹配。
    """
    old_by_anchor: dict[str, tuple[str, str]] = {}
    old_id_hash: dict[str, str] = {}
    for cid, chash, anchor in old:
        old_id_hash[cid] = chash
        if anchor:
            old_by_anchor[anchor] = (cid, chash)

    new_anchors: set[str] = set()
    changes: list[Change] = []
    for cid, chash, anchor in new:
        if anchor:
            new_anchors.add(anchor)
            if anchor in old_by_anchor:
                old_cid, old_hash = old_by_anchor[anchor]
                if chash != old_hash:
                    changes.append(Change(cid, "code", "MODIFIED", file_path, old_hash, chash))
                # 相同则未变，跳过
            else:
                changes.append(Change(cid, "code", "ADDED", file_path, None, chash))
        else:
            # 无锚点：按 chunk_id 匹配（内容变则 chunk_id 已不同→视为新增/删除）
            if cid not in old_id_hash:
                changes.append(Change(cid, "code", "ADDED", file_path, None, chash))

    # 旧锚点消失 → DELETED
    for anchor, (old_cid, old_hash) in old_by_anchor.items():
        if anchor not in new_anchors:
            changes.append(Change(old_cid, "code", "DELETED", file_path, old_hash, None))
    return changes


def _settings_strategy() -> str:
    """延迟读 settings.embedding_strategy（便于测试 monkeypatch）。"""
    from app.core.config import settings
    return settings.embedding_strategy
