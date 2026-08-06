"""主动腐化巡检服务（Phase 7 Milestone 16）。

让 DOC_MAINTAIN 从「被动应答」变「主动巡检」：定时枚举全库非过时 DOC↔CODE 关系，据 ``change_history``
证据用启发式规则判定过时候选并自动标记 ``is_stale=True``（仿 ``sync_soft_delete`` 对 DELETED 的既有
非-HITL 自动标记——HITL 闸门历来只管「重写」，不管「标记」）。重写仍走 M15 的 HITL 闸门；本服务只负责
检测 + 标记 + 可观测，不重写。

判定规则（全库首条自动过时规则，codebase 此前无任何自动过时规则）：非过时 DOC↔CODE 关系，其代码侧
``change_history`` 存在 ``change_type IN ('MODIFIED','DELETED')`` 且 ``git_commit_time > relation.updated_at``
⇒ 过时候选。（ADDED/RESTORED/ROLLBACK 不计；DELETED 路径 soft-delete 已实时处理，巡检补 MODIFIED。）

镜像 ``maintenance_service`` 约定：注入 ``AsyncSession``、raw ``text()`` SQL、函数内 commit、返回计数 dict、
**不自行 log（让 main 巡检循环 log）**、永不抛（catch → rollback + error dict，不杀循环）。
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_STALE_CHANGE_TYPES = ("MODIFIED", "DELETED")

_RELATIONS_SQL = text(
    """
    SELECT relation_id, source_chunk_id, target_chunk_id, relation_type, anchor_key, updated_at
      FROM chunk_relations
     WHERE relation_type IN ('DOC_TO_CODE', 'CODE_TO_DOC') AND is_stale = false
     ORDER BY relation_id
     LIMIT :limit
    """
)
# 代码侧每个 chunk 的最近一次 MODIFIED/DELETED（DISTINCT ON + DESC NULLS LAST 取最新）；命中 idx_history_chunk。
_LATEST_CHANGE_SQL = text(
    """
    SELECT DISTINCT ON (chunk_id) chunk_id, change_type, git_commit_time, git_commit_hash, commit_message
      FROM change_history
     WHERE chunk_id = ANY(cast(:ids as text[])) AND change_type IN ('MODIFIED', 'DELETED')
     ORDER BY chunk_id, git_commit_time DESC NULLS LAST
    """
)
_MARK_SQL = text(
    "UPDATE chunk_relations SET is_stale = true, stale_reason = :reason WHERE relation_id = :rid"
)
_REPORT_SQL = text(
    """
    SELECT
      count(*)                                                            AS total,
      count(*) FILTER (WHERE is_stale)                                    AS stale,
      count(*) FILTER (WHERE is_stale AND stale_reason LIKE 'SWEEP:%')   AS sweep,
      count(*) FILTER (WHERE is_stale AND stale_reason LIKE 'DELETED:%') AS deleted,
      count(*) FILTER (WHERE is_stale AND stale_reason NOT LIKE 'SWEEP:%'
                       AND stale_reason NOT LIKE 'DELETED:%')            AS other
      FROM chunk_relations
     WHERE relation_type IN ('DOC_TO_CODE', 'CODE_TO_DOC')
    """
)
_RECENT_SQL = text(
    """
    SELECT relation_id, relation_type, anchor_key, stale_reason, updated_at
      FROM chunk_relations
     WHERE stale_reason LIKE 'SWEEP:%'
     ORDER BY updated_at DESC
     LIMIT :n
    """
)


def _code_chunk_id(row) -> str | None:
    """按方向约定（maintain_tools/graph_service/doc_maintain 三处一致）派生关系的代码侧 chunk_id。

    DOC_TO_CODE ⇒ source=doc/target=code；CODE_TO_DOC ⇒ source=code/target=doc。
    """
    rel_type = row["relation_type"]
    if rel_type == "DOC_TO_CODE":
        return row["target_chunk_id"]
    if rel_type == "CODE_TO_DOC":
        return row["source_chunk_id"]
    return None  # 查询已过滤为 DOC↔CODE，此处仅防御


def _build_reason(change: dict) -> str:
    """装配 stale_reason：``SWEEP:{type}@{short_hash} {首行 commit msg}``（≤256，仿 DELETED:{commit}）。"""
    short = (change.get("git_commit_hash") or "unknown")[:8]
    msg = (change.get("commit_message") or "").strip()
    if msg:
        msg = msg.splitlines()[0][:80]
    reason = f"SWEEP:{change['change_type']}@{short}"
    if msg:
        reason = f"{reason} {msg}"
    return reason[:256]


async def run_staleness_sweep(session: AsyncSession, *, batch_size: int = 200) -> dict:
    """巡检一轮：枚举非过时 DOC↔CODE 关系，启发式判定过时候选并标记 ``is_stale=True``。

    返回 ``{"scanned", "marked", "by_change_type": {"MODIFIED": n, "DELETED": n}}``。
    幂等（仅扫 ``is_stale=false``，标记后关系出池）；永不抛（异常 → rollback + ``{"error": ...}``，不杀循环）。
    """
    by_type = {"MODIFIED": 0, "DELETED": 0}
    try:
        rels = (await session.execute(_RELATIONS_SQL, {"limit": batch_size})).mappings().all()
        if not rels:
            return {"scanned": 0, "marked": 0, "by_change_type": by_type}

        # 代码侧 chunk_id 去重，一次查 change_history，避免 N+1
        code_ids = sorted({_code_chunk_id(r) for r in rels} - {None})
        latest: dict[str, dict] = {}
        if code_ids:
            for r in (await session.execute(_LATEST_CHANGE_SQL, {"ids": code_ids})).mappings().all():
                latest[r["chunk_id"]] = dict(r)

        marked = 0
        for rel in rels:
            cid = _code_chunk_id(rel)
            change = latest.get(cid) if cid else None
            if not change:
                continue
            ctime = change.get("git_commit_time")
            updated = rel.get("updated_at")
            # 仅当代码侧最近变更晚于关系建立/更新、且有确切时间戳才判过时
            if ctime is None or updated is None or ctime <= updated:
                continue
            await session.execute(_MARK_SQL, {"reason": _build_reason(change), "rid": rel["relation_id"]})
            marked += 1
            if change.get("change_type") in by_type:
                by_type[change["change_type"]] += 1
        await session.commit()
        return {"scanned": len(rels), "marked": marked, "by_change_type": by_type}
    except Exception as e:  # noqa: BLE001  永不抛，不杀循环
        await session.rollback()
        return {"scanned": 0, "marked": 0, "by_change_type": by_type,
                "error": f"{type(e).__name__}: {e}"}


async def build_staleness_report(session: AsyncSession, *, recent: int = 20) -> dict:
    """聚合过时报告（供 ``GET /v1/staleness/report``）。

    返回 ``{"total", "stale", "by_source": {"sweep","deleted","other"}, "recent": [...]}``。
    按 ``stale_reason`` 前缀区分来源（SWEEP=巡检 / DELETED=soft_delete / other=HITL 等）。
    """
    row = (await session.execute(_REPORT_SQL)).mappings().first()
    recents = (await session.execute(_RECENT_SQL, {"n": recent})).mappings().all()
    g = lambda k: int((row or {}).get(k) or 0)  # noqa: E731
    return {
        "total": g("total"),
        "stale": g("stale"),
        "by_source": {"sweep": g("sweep"), "deleted": g("deleted"), "other": g("other")},
        "recent": [dict(r) for r in recents],
    }
