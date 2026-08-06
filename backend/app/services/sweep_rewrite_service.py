"""SWEEP 批量重写服务（Phase 7 Milestone 17）。

桥接 M16（主动腐化巡检：把 DOC↔CODE 关系批量标 ``is_stale=true``、``stale_reason LIKE 'SWEEP:%'``）
→ M15（重写原语 ``generate_doc_update`` / ``create_doc_pr``）。M16 标记的过时关系对反应式 HITL 路径
**不可见**（``detect_stale_docs`` 过滤 ``is_stale=false``），本服务闭合该缺口：为 top-N SWEEP 标记的
过时文档段落**批量**生成重写提案，落 ``doc_update_proposals`` PENDING 行（＝持久审批队列），再逐项
approve/reject（状态翻转）。

架构裁决（Design Q「生成→队列→逐项审批」）：generate 在审批**前**——``generate_doc_update`` 只产
append-only MinIO 工件、``create_doc_pr`` 只 INSERT 一行 PENDING，**均不改 doc_chunks/code/relations
源真相、不 push git**（已逐行核实），故 generate 无需审批；HITL 闸门移到管「是否采纳」
（``set_proposal_status`` approve→``APPROVED`` **触发真写回**：``rewritten_text`` 写回 ``doc_chunks.content``
+ 重算 hash/token + 置 ``embedding_synced=false`` + 清 ``relation_ids`` 过时；**M20 post-commit eager 重嵌入**
（即时 embed + upsert Milvus，成功翻 ``embedding_synced=true``；失败/无密钥降级留懒 flag，resync 兜底）；
**M21 post-commit 真 git**（隔离 worktree 建分支+提交+可选推送 → 回填 ``commit_sha``/``pr_url``、状态翻
``PUSHED``/``COMMITTED``，失败→``PUSH_FAILED``，KB 已写回不受影响）；reject→``REJECTED`` 仅状态翻转）。

镜像 ``staleness_sweep_service`` 约定：注入 ``AsyncSession``、raw ``text()`` SQL、函数内 commit、
返回 dict、**不自行 log（让端点/调用方 log）**、永不抛（catch → rollback + error dict）。
"""
from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import embedding_client
from app.core.config import settings
from app.db.models.history import DocUpdateProposal
from app.pipeline.indexing import index_chunks_to_milvus
from app.pipeline.metadata import approx_token_count, content_hash
from app.services.doc_maintenance_service import create_doc_pr, generate_doc_update
from app.services.doc_pr_service import fulfill_doc_update

# approve 触发真写回 doc_chunks + 清关系（M18）+ post-commit 重嵌入（M20）+ 真 git（M21）；reject 仅状态翻转。
_ALLOWED_DECISIONS = {"APPROVED", "REJECTED"}
# 幂等守卫：已有「待审」提案的 doc_chunk_id 挡重复生成；APPROVED/REJECTED/FAILED 是已决定/失败态，
# 不再占位（approve 后文档已写回、可被再次检测/重写 → 闭环可循环）。
_ACTIVE_STATUSES = ("PENDING_PUSH", "PENDING_MANUAL")

# SWEEP 过时池：唯一含 source_chunk_id 且过滤 is_stale=true 的 chunk_relations SELECT
# （区别于 staleness_sweep._RELATIONS_SQL[is_stale=false] / _RECENT_SQL[无 chunk id] / _REPORT_SQL[FILTER]）。
_SWEEP_POOL_SQL = text(
    """
    SELECT relation_id, source_chunk_id, target_chunk_id, relation_type, anchor_key, stale_reason, updated_at
      FROM chunk_relations
     WHERE relation_type IN ('DOC_TO_CODE', 'CODE_TO_DOC')
       AND is_stale = true
       AND stale_reason LIKE 'SWEEP:%'
     ORDER BY updated_at DESC
     LIMIT :n
    """
)
# 幂等守卫：去重后批量查一次已有 active 提案的 doc_chunk_id（避 N+1）。
_ACTIVE_DOC_CHUNKS_SQL = text(
    """
    SELECT DISTINCT doc_chunk_id
      FROM doc_update_proposals
     WHERE status = ANY(cast(:stats as text[]))
       AND doc_chunk_id = ANY(cast(:ids as text[]))
    """
)
_SET_STATUS_SQL = text(
    "UPDATE doc_update_proposals SET status = :status WHERE proposal_id = :pid"
)
# M18 真写回：approve 时取提案 doc_chunk_id/rewritten_text/relation_ids（text SELECT，区别于 list ORM select）。
_PROPOSAL_FOR_WRITEBACK_SQL = text(
    "SELECT doc_chunk_id, rewritten_text, relation_ids "
    "FROM doc_update_proposals WHERE proposal_id = :pid"
)
# 写回 doc_chunks.content + 重算 hash/token + 置 embedding_synced=false（懒重嵌入：resync 用新 content 重嵌）。
# updated_at 由 onupdate=func.now() 自动触，无需手设。
_WRITEBACK_DOC_SQL = text(
    "UPDATE doc_chunks SET content = :content, content_hash = :hash, "
    "token_count = :tokens, embedding_synced = false WHERE chunk_id = :cid"
)
# M20 eager 重嵌入成功后翻回 embedding_synced=true（与写回的 =false 同列、异步 session 上独立 UPDATE）。
_MARK_SYNCED_SQL = text(
    "UPDATE doc_chunks SET embedding_synced = true WHERE chunk_id = :cid"
)
# 文档已与代码一致 → 清掉该提案锚点关系的过时标记（DOC↔CODE 闭环）。
_CLEAR_RELATIONS_SQL = text(
    "UPDATE chunk_relations SET is_stale = false, stale_reason = NULL "
    "WHERE relation_id = ANY(cast(:ids as bigint[]))"
)


def _doc_code_ids(row) -> tuple[str | None, str | None]:
    """方向约定（maintain_tools / graph_service / doc_maintain 三处一致）：DOC_TO_CODE ⇒
    source=doc/target=code；CODE_TO_DOC ⇒ source=code/target=doc。返回 (doc_chunk_id, code_chunk_id)。

    内联而非导入 ``app.agent.nodes.doc_maintain._split_anchor``——service 层不应依赖 agent node 模块。
    """
    src, tgt = row["source_chunk_id"], row["target_chunk_id"]
    if row["relation_type"] == "CODE_TO_DOC":
        return tgt, src
    return src, tgt


def _proposal_to_dict(r: DocUpdateProposal) -> dict:
    """ORM 行 → ProposalItem 投影（``rewritten_ok`` 由 ``rewritten_text`` 派生，对齐 create_doc_pr）。"""
    return {
        "proposal_id": r.proposal_id,
        "conversation_id": r.conversation_id,
        "file_id": r.file_id,
        "doc_chunk_id": r.doc_chunk_id,
        "heading_path": list(r.heading_path or []),
        "relation_ids": list(r.relation_ids or []),
        "status": r.status,
        "rewritten_ok": bool(r.rewritten_text),
        "artifact_key": r.artifact_key,
        "branch_name": r.branch_name,
        "commit_sha": r.commit_sha,  # M21 真 git 产出
        "pr_url": r.pr_url,
        # M19 审批 UI 预览：原文/重写内容（list 端点填充；create_doc_pr 已存这两列）。
        "rewritten_text": r.rewritten_text,
        "original_text": r.original_text,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


async def run_sweep_rewrite(session: AsyncSession, *, top_n: int = 10) -> dict:
    """为 top-N SWEEP 标记的过时 doc 批量生成重写提案（落 doc_update_proposals PENDING 行）。

    流程：① 池 SELECT（SWEEP 过时 DOC↔CODE 关系，LIMIT top_n）→ ② 按 doc_chunk_id 去重 + 聚合
    relation_ids（镜像 ``apply_stale``，first-seen code 胜）→ ③ 幂等守卫跳过已有 active 提案的段落
    → ④ 逐段落 ``generate_doc_update`` + ``create_doc_pr``（M15 原语，自身永不抛、逐行 commit）。

    返回 ``{scanned, slots, skipped_existing, rewritten, pending_push, pending_manual, failed,
    proposals:[...], error?}``。永不抛（catch → rollback + error dict）。
    """
    counts: dict = {
        "scanned": 0, "slots": 0, "skipped_existing": 0, "rewritten": 0,
        "pending_push": 0, "pending_manual": 0, "failed": 0, "proposals": [],
    }
    try:
        rows = (await session.execute(_SWEEP_POOL_SQL, {"n": top_n})).mappings().all()
        counts["scanned"] = len(rows)

        # ② 去重（同 apply_stale:310-315）
        seen: dict[str, dict] = {}
        for row in rows:
            doc_cid, code_cid = _doc_code_ids(row)
            if doc_cid is None:
                continue
            slot = seen.setdefault(
                doc_cid, {"doc_chunk_id": doc_cid, "code_chunk_id": code_cid, "relation_ids": []}
            )
            slot["relation_ids"].append(row["relation_id"])
        counts["slots"] = len(seen)

        # ③ 幂等守卫（best-effort：守卫查询失败不阻断生成，仅放弃去重）
        active: set[str] = set()
        if seen:
            try:
                arows = (await session.execute(
                    _ACTIVE_DOC_CHUNKS_SQL,
                    {"stats": list(_ACTIVE_STATUSES), "ids": list(seen)},
                )).mappings().all()
                active = {r["doc_chunk_id"] for r in arows}
            except Exception:  # noqa: BLE001
                await session.rollback()
                active = set()

        # ④ 逐段落重写 + 落提案（M15 原语自身永不抛）
        for doc_cid, slot in seen.items():
            if doc_cid in active:
                counts["skipped_existing"] += 1
                continue
            upd = await generate_doc_update(
                session, doc_chunk_id=doc_cid, code_chunk_id=slot["code_chunk_id"]
            )
            pr = await create_doc_pr(
                session, conversation_id=None, file_id=upd["file_id"], doc_chunk_id=doc_cid,
                heading_path=upd["heading_path"], relation_ids=slot["relation_ids"],
                original_text=upd["original_text"], rewritten_text=upd["rewritten_text"],
                artifact_key=upd["artifact_key"],
            )
            status = pr.get("status")
            if status == "PENDING_PUSH":
                counts["pending_push"] += 1
            elif status == "PENDING_MANUAL":
                counts["pending_manual"] += 1
            elif status == "FAILED":
                counts["failed"] += 1
            if upd.get("rewritten_ok"):
                counts["rewritten"] += 1
            counts["proposals"].append({
                "proposal_id": pr.get("proposal_id"), "doc_chunk_id": doc_cid,
                "file_id": upd.get("file_id"), "heading_path": upd.get("heading_path") or [],
                "relation_ids": slot["relation_ids"], "status": status,
                "rewritten_ok": bool(upd.get("rewritten_ok")),
                "artifact_key": pr.get("artifact_key"), "branch_name": pr.get("branch_name"),
            })
        return counts
    except Exception as e:  # noqa: BLE001  池查询/意外失败不抛
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        counts["error"] = f"{type(e).__name__}: {e}"
        return counts


async def list_proposals(
    session: AsyncSession, *, status: str | None = None, offset: int = 0, limit: int = 20,
) -> dict:
    """列出 doc 更新提案（此表首个 SELECT），可选 status 过滤，按 created_at 倒序分页。

    返回 ``{total, items:[...], error?}``。永不抛。
    """
    try:
        q = select(DocUpdateProposal).order_by(DocUpdateProposal.created_at.desc())
        cq = select(func.count()).select_from(DocUpdateProposal)
        if status:
            q = q.where(DocUpdateProposal.status == status)
            cq = cq.where(DocUpdateProposal.status == status)
        rows = (await session.execute(q.offset(offset).limit(limit))).scalars().all()
        total = (await session.execute(cq)).scalar_one()
        return {"total": total, "items": [_proposal_to_dict(r) for r in rows]}
    except Exception as e:  # noqa: BLE001
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"total": 0, "items": [], "error": f"{type(e).__name__}: {e}"}


async def _eager_reembed_doc(
    session: AsyncSession, doc_chunk_id: str, text: str,
) -> str:
    """M20 post-commit eager 重嵌入单条 doc chunk：embed + upsert Milvus + 翻 ``embedding_synced=true``。

    返回三态：``synced``（全成）/ ``failed``（闸门过但 embed/upsert/翻 flag 任一失败，flag 留 false，
    resync 兜底）/ ``lazy``（闸门未过——``eager_reembed_enabled`` 关或嵌入器无密钥，flag 留 false）。
    **永不抛、永不染调用方 error**：失败仅记日志 + 返回 ``failed``（审批已 commit，不受影响）。
    复用 ``indexing.index_chunks_to_milvus``（与 ingest/resync 同一文本源 + upsert 语义，无向量漂移）。
    """
    if not settings.eager_reembed_enabled or not embedding_client.enabled():
        return "lazy"
    try:
        # 全位置参数（asyncio.to_thread 不能收 kw-only，见 CLAUDE.md）；index_chunks_to_milvus 同步、自吞。
        ok = await asyncio.to_thread(
            index_chunks_to_milvus, settings.embedding_strategy, "doc",
            [{"chunk_id": doc_chunk_id, "text": text}],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[sweep_rewrite] eager embed 失败 cid={doc_chunk_id} {type(e).__name__}: {e}")
        return "failed"
    if not ok:
        return "failed"
    try:
        await session.execute(_MARK_SYNCED_SQL, {"cid": doc_chunk_id})
        await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[sweep_rewrite] mark-synced 失败 cid={doc_chunk_id} {type(e).__name__}: {e}")
        return "failed"
    return "synced"


async def set_proposal_status(
    session: AsyncSession, *, proposal_id: int, status: str,
) -> dict:
    """人工审批：approve→``APPROVED``（**真写回** ``rewritten_text`` 到 ``doc_chunks.content`` + 重算
    ``content_hash``/``token_count`` + 置 ``embedding_synced=false`` + 清 ``relation_ids`` 的
    ``is_stale``；**post-commit eager 重嵌入**——成功翻 ``embedding_synced=true``，失败/无密钥降级留懒 flag；
    **post-commit 真 git**（M21）——隔离 worktree 建分支+提交+可选推送，回填 ``commit_sha``/``pr_url``、
    状态翻 ``PUSHED``/``COMMITTED``（失败→``PUSH_FAILED``，KB 已写回））；
    reject→``REJECTED``（仅状态翻转）。

    单事务 commit 一次（写回+清关系+状态翻转）：任一步失败→rollback 全回滚（含 status 翻转），保证
    ``APPROVED`` 恒等于「已写入」；eager 重嵌入 + 真 git 均在 commit **之后**post-commit 尽力执行，
    永不抛、永不染 error（否则 ``/decide`` 误判 400 掩盖已成功的 KB 写回）。
    返回 ``{proposal_id, status, applied, doc_chunk_id, relations_cleared, reembed_status,
    git_status, commit_sha, pr_url}`` 或 ``{proposal_id, error}``（invalid status / not found /
    no rewrite to apply / doc chunk not found / DB 错）。``reembed_status`` ∈ ``synced``/``failed``/
    ``lazy``/``None``；``git_status`` ∈ ``PUSHED``/``COMMITTED``/``PUSH_FAILED``/``None``（REJECTED 或未 apply）。
    永不抛。
    """
    if status not in _ALLOWED_DECISIONS:
        return {"proposal_id": proposal_id, "error": "invalid status"}
    applied = False
    doc_chunk_id: str | None = None
    rewritten_text: str | None = None
    relations_cleared = 0
    try:
        if status == "APPROVED":
            row = (await session.execute(
                _PROPOSAL_FOR_WRITEBACK_SQL, {"pid": proposal_id}
            )).mappings().first()
            if row is None:
                return {"proposal_id": proposal_id, "error": "not found"}
            if not row["rewritten_text"]:
                # PENDING_MANUAL（无 LLM 重写）：无可写内容，approve 被拒，status 不变。
                return {"proposal_id": proposal_id, "error": "no rewrite to apply"}
            doc_chunk_id = row["doc_chunk_id"]
            rewritten_text = row["rewritten_text"]
            dres = await session.execute(_WRITEBACK_DOC_SQL, {
                "content": row["rewritten_text"],
                "hash": content_hash(row["rewritten_text"]),
                "tokens": approx_token_count(row["rewritten_text"]),
                "cid": doc_chunk_id,
            })
            if dres.rowcount == 0:
                # doc_chunk_id dangling（无 FK；doc 重入库后 chunk_id 含 hash 会变）→ 无法写回。
                try:
                    await session.rollback()
                except Exception:  # noqa: BLE001
                    pass
                return {"proposal_id": proposal_id, "error": "doc chunk not found"}
            applied = True
            rids = list(row["relation_ids"] or [])
            if rids:
                rres = await session.execute(_CLEAR_RELATIONS_SQL, {"ids": rids})
                relations_cleared = rres.rowcount
        # 状态翻转（APPROVED/REJECTED 共用；REJECTED 的唯一存在性检查）。
        sres = await session.execute(_SET_STATUS_SQL, {"status": status, "pid": proposal_id})
        if sres.rowcount == 0:
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001
                pass
            return {"proposal_id": proposal_id, "error": "not found"}
        await session.commit()
        result = {
            "proposal_id": proposal_id, "status": status, "applied": applied,
            "doc_chunk_id": doc_chunk_id, "relations_cleared": relations_cleared,
        }
    except Exception as e:  # noqa: BLE001
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"proposal_id": proposal_id, "error": f"{type(e).__name__}: {e}"}
    # M20 post-commit eager 重嵌入（仅 APPROVED+applied）：永不抛、永不染 error（见 _eager_reembed_doc）。
    result["reembed_status"] = (
        await _eager_reembed_doc(session, doc_chunk_id, rewritten_text)
        if applied and doc_chunk_id is not None else None
    )
    # M21 post-commit 真 git（仅 APPROVED+applied）：永不抛、永不染 error（见 fulfill_doc_update）。
    # 隔离 worktree 建分支+提交+可选推送 → 回填 commit_sha/pr_url、状态翻 PUSHED/COMMITTED/PUSH_FAILED。
    if applied:
        git = await fulfill_doc_update(session, proposal_id)
        result["git_status"] = git["git_status"]
        result["commit_sha"] = git["commit_sha"]
        result["pr_url"] = git["pr_url"]
        if git["git_status"]:  # 反映真实终态（git 未跑/未起 → 维持 APPROVED）
            result["status"] = git["git_status"]
    else:
        result["git_status"] = None
        result["commit_sha"] = None
        result["pr_url"] = None
    return result
