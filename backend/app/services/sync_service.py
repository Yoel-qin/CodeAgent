"""增量/全量同步编排（设计 §13.2 + §18）。

单一入口 :func:`run_sync` 被 CLI（``scripts/sync_repo.py``）与 API（``api/v1/sync.py``）共用：
建 ``sync_tasks`` 行（PENDING→RUNNING→COMPLETED/FAILED），跑 FULL（复用 ``ingest.ingest_repo``）
或 INCREMENTAL（git diff → 逐文件 ADDED/MODIFIED/DELETED → 回滚分类/恢复），聚合 counts 与
``change_details`` JSONB。

关键点：
- **进入 RUNNING 即提交任务行**——``ingest_repo`` 的 per-file ``rollback()`` 会回滚到最近提交，
  预提交使任务行在漫长 FULL 入库中幸存，且 API 可见 RUNNING 任务。
- **INCREMENTAL 用 savepoint（``begin_nested``）隔离 per-file 失败**——单文件出错只回滚该文件，
  外层事务与已处理文件不受影响（优于 ingest_repo 的全量 rollback）；失败记入 ``change_details.errors``。
- **无 COMPLETED 游标时 INCREMENTAL 自动回退 FULL**（首次同步）。
- ``run_sync_on_engine`` 是位置参数包装，供 ``asyncio.to_thread`` 调用（CLAUDE.md：to_thread
  不能传 keyword-only 参数）。
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import ChangeHistory, CodeChunk, CodeFile, DocChunk, DocFile, SyncTask
from app.pipeline import ingest, relations
from app.pipeline.sync_git import changed_files, commit_meta, git_head, resolve_commit
from app.pipeline.sync_incremental import Change, apply_added_or_modified
from app.pipeline.sync_rollback import apply_rollback_restore, classify_rollbacks
from app.pipeline.sync_soft_delete import soft_delete_file
from app.services.doc_pr_service import close_open_doc_pr_for

_FILE_MODEL = {"code": CodeFile, "doc": DocFile}
_CHUNK_MODEL = {"code": CodeChunk, "doc": DocChunk}

_engine = None


def get_sync_engine():
    """懒加载同步 engine（psycopg，与 Alembic 同驱动；CLI/API 共用）。"""
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url_sync)
    return _engine


def _parse_time(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _last_completed_commit(session: Session) -> str | None:
    """最近一条 COMPLETED sync_tasks 的 commit_hash（增量游标）。"""
    row = session.execute(
        select(SyncTask.commit_hash).where(SyncTask.status == "COMPLETED")
        .order_by(SyncTask.created_at.desc()).limit(1)
    ).first()
    return row[0] if row else None


def _active_chunk_info(session: Session, kind: str, file_path: str) -> list[tuple[str, str]]:
    """某文件当前活跃 chunk 的 (chunk_id, content_hash)——供 DELETED 写 change_history。"""
    file_model = _FILE_MODEL[kind]
    chunk_model = _CHUNK_MODEL[kind]
    fid = session.execute(
        select(file_model.file_id).where(file_model.file_path == file_path)
    ).scalar_one_or_none()
    if fid is None:
        return []
    return list(session.execute(
        select(chunk_model.chunk_id, chunk_model.content_hash).where(
            chunk_model.file_id == fid, chunk_model.is_deleted == False  # noqa: E712
        )
    ).all())


def _write_change_history(
    session: Session, changes: list[Change], new_commit: str, meta: dict,
    commit_time: datetime | None,
) -> None:
    """把（经回滚分类后的）变更写入 change_history。old/new_content 正文不存（hash 已够检测）。"""
    msg = (meta.get("message") or "")[:512]
    for c in changes:
        session.add(ChangeHistory(
            chunk_id=c.chunk_id, chunk_type=c.chunk_type, change_type=c.change_type,
            old_content_hash=c.old_content_hash, new_content_hash=c.new_content_hash,
            git_commit_hash=new_commit, git_commit_time=commit_time,
            git_author=meta.get("author"), commit_message=msg,
            rollback_source_commit=c.rollback_source_commit,
            is_rollback_related=c.is_rollback_related,
        ))


def _relations_total(rel: dict | None) -> int:
    """relations.build_all 的返回是嵌套 dict（``anchors``/``call_graph`` 各为 dict），
    逐字段取整数求和；对历史假设（``anchors`` 为 int）亦兼容。"""
    if not rel:
        return 0
    total = 0
    anc = rel.get("anchors")
    if isinstance(anc, dict):
        total += int(anc.get("relations", 0) or 0)
        total += int(anc.get("anchor_mappings", 0) or 0)
    elif isinstance(anc, int):
        total += anc
    cg = rel.get("call_graph")
    if isinstance(cg, dict):
        total += int(cg.get("call_edges", 0) or 0)
    return total


def _run_full(session: Session, repo: Path, new_commit: str, task: SyncTask,
              build_relations: bool | None) -> None:
    br = settings.sync_rebuild_relations_on_full if build_relations is None else build_relations
    stats = ingest.ingest_repo(
        session, repo, module=None, commit_hash=new_commit, build_relations=br)
    code = stats.get("code", {})
    doc = stats.get("doc", {})
    task.files_changed = code.get("files", 0) + doc.get("files", 0)
    task.chunks_added = code.get("chunks", 0) + doc.get("chunks", 0)
    task.chunks_modified = 0
    task.chunks_deleted = 0
    task.relations_updated = _relations_total(stats.get("relations"))
    task.vector_sync_status = "COMPLETED"   # best-effort；未同步由 resync 循环补偿
    task.graph_update_status = "COMPLETED" if br else "SKIPPED"
    cd = dict(task.change_details or {})
    cd["type"] = "FULL"
    cd["errors"] = stats.get("errors", [])
    task.change_details = cd


def _run_incremental(session: Session, repo: Path, new_commit: str, task: SyncTask) -> None:
    cursor = _last_completed_commit(session)
    if cursor is None or resolve_commit(repo, cursor) is None:
        reason = "no_cursor" if cursor is None else "cursor_unresolvable"
        logger.info(f"[sync] INCREMENTAL 无可用游标（{reason}），回退 FULL")
        _run_full(session, repo, new_commit, task, None)
        cd = dict(task.change_details or {})
        cd.update({"type": "INCREMENTAL", "fallback": "FULL", "reason": reason})
        task.change_details = cd
        return

    meta = commit_meta(repo, new_commit)
    commit_time = _parse_time(meta["time"])
    files = changed_files(repo, cursor, new_commit)
    relevant = [fc for fc in files if fc.kind is not None]
    logger.info(f"[sync] INCREMENTAL {cursor[:8]}→{new_commit[:8]}: {len(relevant)} 个相关文件")

    all_changes: list[Change] = []
    counts = {"added": 0, "modified": 0, "deleted": 0, "relations": 0}
    errors: list[dict] = []
    files_changed = 0
    for fc in relevant:
        files_changed += 1
        try:
            with session.begin_nested():  # savepoint：单文件失败只回滚该文件
                if fc.change == "DELETED":
                    info = _active_chunk_info(session, fc.kind, fc.file_path)
                    sd = soft_delete_file(
                        session, file_path=fc.file_path, kind=fc.kind,
                        delete_commit=new_commit, commit_time=commit_time)
                    counts["deleted"] += sd["chunks"]
                    counts["relations"] += sd["relations"]
                    for cid, chash in info:
                        all_changes.append(Change(cid, fc.kind, "DELETED", fc.file_path,
                                                  old_content_hash=chash))
                else:
                    summary, changes = apply_added_or_modified(
                        session, repo, fc, new_commit=new_commit)
                    counts["added"] += summary["added"]
                    counts["modified"] += summary["modified"]
                    counts["deleted"] += summary["deleted"]
                    all_changes.extend(changes)
        except Exception as e:  # savepoint 已回滚；外层事务与已处理文件不受影响
            logger.warning(f"[sync] 文件处理失败 {fc.file_path}: {type(e).__name__}: {e}")
            errors.append({"file": fc.file_path, "change": fc.change,
                           "error": f"{type(e).__name__}: {e}"[:200]})

    classify_rollbacks(session, all_changes, commit_message=meta.get("message", ""))
    rollback_rows = apply_rollback_restore(
        session, all_changes, new_commit=new_commit,
        doc_pr_closer=close_open_doc_pr_for, commit_time=commit_time)
    for rb in rollback_rows:
        session.add(rb)

    # §18 修复：回滚时整文件重入库（apply_added_or_modified→replace_chunks→clear_code_chunk_refs）
    # 会硬删掉 soft_delete_file 打过 stale/inactive/deleted 标记的 关系/锚点/调用图行，致
    # apply_rollback_restore 无行可翻（relations/anchors_restored 恒 0）、回滚后知识图谱空至下次 FULL。
    # 检测到回滚即重建关联：build_all 仅按「活跃 chunk」建边，不复活软删 chunk，终态正确。
    rebuilt = _relations_total(relations.build_all(session, repo_path=repo)) if rollback_rows else 0

    _write_change_history(session, all_changes, new_commit, meta, commit_time)

    task.files_changed = files_changed
    task.chunks_added = counts["added"]
    task.chunks_modified = counts["modified"]
    task.chunks_deleted = counts["deleted"]
    task.relations_updated = counts["relations"] + rebuilt
    task.vector_sync_status = "COMPLETED"
    task.graph_update_status = "COMPLETED" if rebuilt else "SKIPPED"
    task.change_details = {
        "type": "INCREMENTAL", "cursor": cursor,
        "changes": [{"chunk_id": c.chunk_id, "file": c.file_path, "change_type": c.change_type,
                     "rollback_source_commit": c.rollback_source_commit} for c in all_changes],
        "rollbacks": len(rollback_rows), "errors": errors,
    }


def run_sync(
    session: Session, repo_path: str | Path, *, type: str,
    target_commit: str | None = None, triggered_by: str = "MANUAL",
    build_relations: bool | None = None,
) -> SyncTask:
    """跑一次同步（FULL 或 INCREMENTAL），返回最终 SyncTask（已提交）。

    失败：任务标 FAILED（error_message 落库）后重新抛出，供 CLI/API 感知。
    """
    repo = Path(repo_path)
    if target_commit:
        resolved = resolve_commit(repo, target_commit)
        if not resolved:
            raise ValueError(f"target_commit {target_commit!r} 不是仓库中的有效提交")
        new_commit = resolved
    else:
        new_commit = git_head(repo)

    task = SyncTask(commit_hash=new_commit, status="PENDING",
                    change_details={"type": type}, vector_sync_status="PENDING",
                    graph_update_status="PENDING")
    session.add(task)
    session.flush()
    task_id = task.task_id
    task.started_at = datetime.now(UTC)
    task.status = "RUNNING"
    session.commit()  # 预提交：使任务行在漫长 FULL 入库（其 per-file rollback）中幸存

    try:
        if type == "FULL":
            _run_full(session, repo, new_commit, task, build_relations)
        elif type == "INCREMENTAL":
            _run_incremental(session, repo, new_commit, task)
        else:
            raise ValueError(f"未知同步类型 {type!r}（仅支持 FULL / INCREMENTAL）")
        # 统一把 triggered_by 并入 change_details（_run_* 可能重建过该 dict）
        cd = dict(task.change_details or {})
        cd["triggered_by"] = triggered_by
        task.change_details = cd
        task.status = "COMPLETED"
        task.completed_at = datetime.now(UTC)
        session.commit()
    except Exception as e:
        session.rollback()
        t = session.get(SyncTask, task_id)
        if t is not None:
            t.status = "FAILED"
            # 注意：run_sync 的 ``type`` 形参遮蔽了内置 ``type``，故用 ``e.__class__.__name__``
            t.error_message = f"{e.__class__.__name__}: {e}"[:2000]
            t.completed_at = datetime.now(UTC)
            session.commit()
        logger.exception(f"[sync] 任务 {task_id} FAILED: {e}")
        return session.get(SyncTask, task_id)
    return session.get(SyncTask, task_id)


def run_sync_on_engine(
    engine, repo_path, sync_type, target_commit=None, triggered_by="MANUAL",
):
    """``run_sync`` 的位置参数包装——开同步 Session 跑一次。供 ``asyncio.to_thread`` 用。

    参数全为位置或关键字（无 keyword-only），规避 ``asyncio.to_thread`` 传 kwargs 报错的坑。
    """
    with Session(engine) as session:
        return run_sync(
            session, repo_path, type=sync_type, target_commit=target_commit,
            triggered_by=triggered_by,
        )
