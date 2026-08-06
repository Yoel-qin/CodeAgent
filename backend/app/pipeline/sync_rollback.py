"""§18 回滚检测与恢复。

**检测**（§18 四.1）：在增量变更分类（ADDED/MODIFIED/DELETED）之上，查 ``change_history``
把两类变更重标记——
- ``MODIFIED`` 且其 ``new_content_hash`` 命中该 chunk 历史某行的 ``old_content_hash`` → **ROLLBACK**
  （内容改回旧版本；``rollback_source_commit`` = 那行历史记录的 commit）。原理：code ``chunk_id``
  含 content_hash，内容还原会复现原 ``chunk_id``，故能命中历史行。
- ``ADDED`` 且该 ``chunk_id`` 有历史 ``DELETED`` 行 → **RESTORED**（被删 chunk 重新出现）。
辅助提示：提交信息含 ``Revert``/``回滚``（仅日志，不单独判定）。

**恢复**（§18 四.2）：按 ``source_commit`` 把 §6.4 软删除级联打的标记翻回——
① chunk_relations.is_stale 清零 ② anchor_mappings.is_active 重激活 ③ doc stale_anchors 移除
④ call_graph.is_deleted 翻回。⑤⑦（Milvus/ES 重 embed/重索引）**已由增量 ingest 步骤完成**
（被还原的文件经 ``apply_added_or_modified`` 重新入库 → 重新 embed + 重索引），此处不重复。
⑥ GNN 已弃用（2026-07-27），跳过。⑧ 关 doc PR 由调用方注入的 ``doc_pr_closer`` 打桩处理。
⑨ 写 ``rollback_history`` 审计行（每个 source_commit 一行）。
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import AnchorMapping, CallGraph, ChangeHistory, ChunkRelation, DocChunk, RollbackHistory
from app.pipeline.sync_incremental import Change

_REVERT_HINTS = ("revert", "回滚", "rollback")


def _is_revert_message(message: str) -> bool:
    m = (message or "").lower()
    return any(h in m for h in _REVERT_HINTS)


def _match_rollback_source(session: Session, chunk_id: str, new_hash: str) -> str | None:
    """MODIFIED→ROLLBACK：new_hash 命中该 chunk 历史某行 old_content_hash → 返回那行 commit。"""
    if not new_hash:
        return None
    row = session.execute(
        select(ChangeHistory.git_commit_hash).where(
            ChangeHistory.chunk_id == chunk_id,
            ChangeHistory.old_content_hash == new_hash,
        ).order_by(ChangeHistory.created_at.desc()).limit(1)
    ).first()
    return row[0] if row else None


def _match_restore_source(session: Session, chunk_id: str) -> str | None:
    """ADDED→RESTORED：该 chunk_id 有历史 DELETED 行 → 返回那行 commit。"""
    row = session.execute(
        select(ChangeHistory.git_commit_hash).where(
            ChangeHistory.chunk_id == chunk_id,
            ChangeHistory.change_type == "DELETED",
        ).order_by(ChangeHistory.created_at.desc()).limit(1)
    ).first()
    return row[0] if row else None


def classify_rollbacks(
    session: Session, changes: list[Change], *, commit_message: str = "",
) -> list[Change]:
    """对增量分类结果做回滚重标记（原地修改并返回 ``changes``）。

    查询 ``change_history``：MODIFIED 命中历史 old_hash → ROLLBACK；ADDED 有历史 DELETED → RESTORED。
    """
    if _is_revert_message(commit_message):
        logger.info("[rollback] 提交信息含 revert 提示，增强回滚判定")
    for c in changes:
        if c.change_type == "MODIFIED":
            src = _match_rollback_source(session, c.chunk_id, c.new_content_hash or "")
            if src:
                c.change_type = "ROLLBACK"
                c.rollback_source_commit = src
                c.is_rollback_related = True
        elif c.change_type == "ADDED":
            src = _match_restore_source(session, c.chunk_id)
            if src:
                c.change_type = "RESTORED"
                c.rollback_source_commit = src
                c.is_rollback_related = True
    return changes


def apply_rollback_restore(
    session: Session, changes: list[Change], *, new_commit: str,
    doc_pr_closer: Callable[[str], str | None] | None = None,
    commit_time: datetime | None = None,
) -> list[RollbackHistory]:
    """对已分类的 ROLLBACK/RESTORED 变更执行 §18 四.2 恢复，返回新增的 RollbackHistory 行。

    无回滚变更时返回空列表。按 ``rollback_source_commit`` 分组，每组一条审计行。
    """
    by_source: dict[str, list[Change]] = defaultdict(list)
    for c in changes:
        if c.rollback_source_commit and c.change_type in ("ROLLBACK", "RESTORED"):
            by_source[c.rollback_source_commit].append(c)
    if not by_source:
        return []

    rows: list[RollbackHistory] = []
    for source_commit, group in by_source.items():
        rolled_back = sum(1 for c in group if c.change_type == "ROLLBACK")
        restored = sum(1 for c in group if c.change_type == "RESTORED")

        # ① chunk_relations：清掉因该 source 而置 stale 的关联
        rel_res = session.execute(
            update(ChunkRelation).where(
                ChunkRelation.is_stale == True,  # noqa: E712
                ChunkRelation.stale_reason.like(f"%{source_commit}%"),
            ).values(is_stale=False, stale_reason=None)
        )
        relations_restored = rel_res.rowcount or 0

        # ② anchor_mappings：重激活因该 source 停用的锚点；先取 (doc_chunk_id, anchor_key) 供 ③
        reactivated = session.execute(
            select(AnchorMapping.doc_chunk_id, AnchorMapping.anchor_key).where(
                AnchorMapping.is_active == False,  # noqa: E712
                AnchorMapping.deactivated_by_commit == source_commit,
            )
        ).all()
        anc_res = session.execute(
            update(AnchorMapping).where(
                AnchorMapping.is_active == False,  # noqa: E712
                AnchorMapping.deactivated_by_commit == source_commit,
            ).values(is_active=True, deactivated_at=None, deactivated_by_commit=None)
        )
        anchors_restored = anc_res.rowcount or 0

        # ③ doc_chunks.stale_anchors：移除重激活的锚点
        stale_cleared = _clear_doc_stale_anchors(session, reactivated)

        # ④ call_graph：翻回因该 source 软删的边
        session.execute(
            update(CallGraph).where(
                CallGraph.is_deleted == True,  # noqa: E712
                CallGraph.git_commit_hash == source_commit,
            ).values(is_deleted=False)
        )

        # ⑧ 关 doc PR（打桩；由调用方注入 closer，默认跳过）
        doc_pr_closed = None
        if doc_pr_closer is not None:
            try:
                doc_pr_closed = doc_pr_closer(source_commit)
            except Exception as e:
                logger.warning(f"[rollback] doc_pr_closer 失败 source={source_commit}: {e}")

        # ⑨ 审计行
        rows.append(RollbackHistory(
            rollback_commit=new_commit, source_commit=source_commit,
            chunks_rolled_back=rolled_back, chunks_restored=restored,
            relations_restored=relations_restored, anchors_restored=anchors_restored,
            stale_anchors_cleared=stale_cleared, doc_pr_closed=doc_pr_closed,
            triggered_by="MANUAL", status="COMPLETED",
        ))
    return rows


def _clear_doc_stale_anchors(
    session: Session, reactivated: list[tuple],
) -> int:
    """从存活 doc chunk 的 stale_anchors 移除重激活的锚点，返回清理条数。"""
    by_doc: dict[str, set[str]] = defaultdict(set)
    for doc_id, anchor_key in reactivated:
        if doc_id and anchor_key:
            by_doc[str(doc_id)].add(anchor_key)
    cleared = 0
    for doc_id, keys in by_doc.items():
        dc = session.get(DocChunk, doc_id)
        if dc is None or dc.is_deleted:
            continue
        existing = dc.stale_anchors or []
        filtered = [a for a in existing if a not in keys]
        if len(filtered) != len(existing):
            dc.stale_anchors = filtered
            cleared += len(existing) - len(filtered)
    return cleared
