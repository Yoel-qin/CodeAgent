"""DELETED 路径的软删除级联（设计 §6.4 关联失效处理）。

git diff 检测到某文件被删除时，**不硬删** PG 行——而是把 ``code_chunks``/``doc_chunks`` 的
``is_deleted`` 置真（行保留，使后续回滚 RESTORED 能按 ``chunk_id``（PK）重激活而非重插），
并把关联的 ``chunk_relations.is_stale`` / ``anchor_mappings.is_active`` / ``call_graph.is_deleted``
按 ``delete_commit`` 打标记——这正是 §18 四.2 回滚恢复要按 ``source_commit`` 翻回的标记。

Milvus / ES 无 ``is_deleted`` 概念，故**硬删除**（向量/文档直接删；§18 ⑤ 的「is_deleted=TRUE」
字面在 Milvus 不可实现，RESTORED 时由 ``sync_rollback`` 重新 embed + upsert 补回）。

与 MODIFIED 的 ``replace_chunks`` 硬删除热路径**互不干扰**——这是独立的新函数。
跨库一致性：PG 标记同事务原子提交；Milvus/ES 自吞异常（best-effort），失败靠
``embedding_synced=False`` + ``resync_pending_embeddings`` 补偿。
"""
from __future__ import annotations

from datetime import datetime

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.clients import es_client
from app.core.config import settings
from app.db.models import (
    AnchorMapping,
    CallGraph,
    ChunkRelation,
    CodeChunk,
    CodeFile,
    DocChunk,
    DocFile,
)
from app.pipeline import indexing

_FILE_MODEL = {"code": CodeFile, "doc": DocFile}
_CHUNK_MODEL = {"code": CodeChunk, "doc": DocChunk}


def _get_file_row(session: Session, kind: str, file_path: str):
    """按 file_path 取文件行（code_files/doc_files）。抽出便于测试 monkeypatch。"""
    file_model = _FILE_MODEL[kind]
    return session.execute(
        select(file_model).where(file_model.file_path == file_path)
    ).scalar_one_or_none()


def _chunk_ids(session: Session, kind: str, file_id: int) -> list[str]:
    model = _CHUNK_MODEL[kind]
    rows = session.execute(
        select(model.chunk_id).where(model.file_id == file_id, model.is_deleted == False)  # noqa: E712
    ).scalars().all()
    return list(rows)


def soft_delete_file(
    session: Session, *, file_path: str, kind: str, delete_commit: str,
    commit_time: datetime | None = None,
) -> dict:
    """对单个被删文件执行 §6.4 软删除级联。返回各项计数。

    幂等：文件不存在或无未删 chunk 时返回全 0。Milvus/ES 删除失败仅记日志不抛。
    """
    file_model = _FILE_MODEL[kind]
    chunk_model = _CHUNK_MODEL[kind]
    f = _get_file_row(session, kind, file_path)
    if f is None:
        return {"chunks": 0, "relations": 0, "anchors": 0, "call_edges": 0, "chunk_ids": []}

    chunk_ids = _chunk_ids(session, kind, f.file_id)
    if not chunk_ids:
        # 文件行仍在但无活跃 chunk——仅标记文件删除
        session.execute(update(file_model).where(file_model.file_path == file_path).values(
            is_deleted=True, last_commit=delete_commit))
        return {"chunks": 0, "relations": 0, "anchors": 0, "call_edges": 0, "chunk_ids": []}

    stale_reason = f"DELETED:{delete_commit}"

    # ① chunks 软删（行保留）；code 有 deleted_at，doc 无（schema 不对称）
    chunk_vals: dict = {"is_deleted": True, "deleted_at_commit": delete_commit,
                        "git_commit_hash": delete_commit}
    if commit_time is not None:
        chunk_vals["git_commit_time"] = commit_time
        if kind == "code":
            chunk_vals["deleted_at"] = commit_time
    session.execute(update(chunk_model).where(chunk_model.chunk_id.in_(chunk_ids)).values(**chunk_vals))

    # ② chunk_relations 打 stale（source 或 target 命中）
    rel_res = session.execute(
        update(ChunkRelation)
        .where(
            ChunkRelation.is_stale == False,  # noqa: E712
            (ChunkRelation.source_chunk_id.in_(chunk_ids) | ChunkRelation.target_chunk_id.in_(chunk_ids)),
        ).values(is_stale=True, stale_reason=stale_reason)
    )
    relations = rel_res.rowcount or 0

    # ③ 收集将被停用的锚点映射（code 侧 → 影响 doc stale_anchors；§6.4 ④）
    anchor_rows = session.execute(
        select(AnchorMapping.doc_chunk_id, AnchorMapping.anchor_key).where(
            AnchorMapping.is_active == True,  # noqa: E712
            (AnchorMapping.code_chunk_id.in_(chunk_ids) | AnchorMapping.doc_chunk_id.in_(chunk_ids)),
        )
    ).all()
    doc_anchor_updates: dict[str, set[str]] = {}
    for doc_id, anchor_key in anchor_rows:
        if doc_id and anchor_key and doc_id not in chunk_ids:
            doc_anchor_updates.setdefault(doc_id, set()).add(anchor_key)

    # ④ anchor_mappings 停用
    anc_res = session.execute(
        update(AnchorMapping)
        .where(
            AnchorMapping.is_active == True,  # noqa: E712
            (AnchorMapping.code_chunk_id.in_(chunk_ids) | AnchorMapping.doc_chunk_id.in_(chunk_ids)),
        ).values(is_active=False, deactivated_by_commit=delete_commit,
                 deactivated_at=commit_time)
    )
    anchors = anc_res.rowcount or 0

    # ⑤ doc_chunks.stale_anchors 追加（仅作用于存活 doc chunk；§6.4 ④）
    for doc_id, keys in doc_anchor_updates.items():
        dc = session.get(DocChunk, doc_id)
        if dc is None or dc.is_deleted:
            continue
        existing = set(dc.stale_anchors or [])
        dc.stale_anchors = sorted(existing | keys)

    # ⑥ call_graph 边软删（仅 code；按 source_commit 标记供回滚翻回）
    call_edges = 0
    if kind == "code":
        cg_res = session.execute(
            update(CallGraph)
            .where(
                CallGraph.is_deleted == False,  # noqa: E712
                CallGraph.caller_chunk_id.in_(chunk_ids) | CallGraph.callee_chunk_id.in_(chunk_ids),
            ).values(is_deleted=True, git_commit_hash=delete_commit)
        )
        call_edges = cg_res.rowcount or 0

    # ⑦ Milvus 硬删（自吞）
    try:
        indexing.delete_chunks_from_milvus(settings.embedding_strategy, kind, chunk_ids)
    except Exception as e:  # delete_chunks_from_milvus 已自吞，这里双保险
        logger.warning(f"[soft_delete] Milvus 删除失败 {file_path}: {type(e).__name__}: {e}")

    # ⑧ ES 硬删（delete_by_file 自吞）
    try:
        es_client.delete_by_file(file_path)
    except Exception as e:
        logger.warning(f"[soft_delete] ES 删除失败 {file_path}: {type(e).__name__}: {e}")

    # ⑨ 文件行软删
    session.execute(update(file_model).where(file_model.file_path == file_path).values(
        is_deleted=True, last_commit=delete_commit))

    return {"chunks": len(chunk_ids), "relations": relations, "anchors": anchors,
            "call_edges": call_edges, "chunk_ids": chunk_ids}
