"""同步管理模块路由（api接口清单 §六）：状态/任务/回滚/触发/变更历史。

读路径走 AsyncSession；``POST /trigger`` 通过 ``asyncio.to_thread`` 调同步服务（位置参数包装，
规避 to_thread 不能传 keyword-only 参数的坑，CLAUDE.md）。``type`` 字段存于 ``sync_tasks.change_details``
JSONB（无独立列，免迁移）。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, pagination
from app.core.config import settings
from app.db.models import (
    AnchorMapping,
    ChangeHistory,
    ChunkRelation,
    CodeChunk,
    DocChunk,
    RollbackHistory,
    SyncTask,
)
from app.schemas.sync import (
    ChangeDetailItem,
    ChangeHistoryItem,
    ChangeHistoryResponse,
    ChangeSummary,
    RollbackDetail,
    RollbackItem,
    RollbackListResponse,
    SyncStats,
    SyncStatusResponse,
    SyncTaskDetailResponse,
    SyncTaskItem,
    SyncTaskListResponse,
    TriggerRequest,
    TriggerResponse,
)
from app.services import sync_service

router = APIRouter(prefix="/sync", tags=["sync"])


def _duration_ms(task: SyncTask) -> int | None:
    if task.started_at and task.completed_at:
        return int((task.completed_at - task.started_at).total_seconds() * 1000)
    return None


def _to_task_item(task: SyncTask, rb: RollbackHistory | None = None) -> SyncTaskItem:
    cd = task.change_details or {}
    detail = None
    source = None
    if rb:
        source = rb.source_commit
        detail = RollbackDetail(
            chunks_rolled_back=rb.chunks_rolled_back, chunks_restored=rb.chunks_restored,
            relations_restored=rb.relations_restored, anchors_restored=rb.anchors_restored,
            stale_anchors_cleared=rb.stale_anchors_cleared,
        )
    return SyncTaskItem(
        task_id=task.task_id, type=cd.get("type", "FULL"), commit=task.commit_hash,
        status=task.status,
        changes=ChangeSummary(added=task.chunks_added, modified=task.chunks_modified,
                              deleted=task.chunks_deleted),
        started_at=task.started_at, finished_at=task.completed_at,
        duration_ms=_duration_ms(task), doc_pr_url=task.doc_pr_url,
        doc_pr_status=task.doc_pr_status, source_commit=source, rollback_detail=detail,
        triggered_by=(rb.triggered_by if rb else cd.get("triggered_by")),
    )


@router.get("/status", response_model=SyncStatusResponse)
async def sync_status(session: AsyncSession = Depends(get_db)) -> SyncStatusResponse:
    code = (await session.execute(
        select(func.count()).select_from(CodeChunk).where(CodeChunk.is_deleted == False)  # noqa: E712
    )).scalar_one()
    doc = (await session.execute(
        select(func.count()).select_from(DocChunk).where(DocChunk.is_deleted == False)  # noqa: E712
    )).scalar_one()
    stale = (await session.execute(
        select(func.count()).select_from(DocChunk).where(
            DocChunk.is_deleted == False,  # noqa: E712
            func.jsonb_array_length(DocChunk.stale_anchors) > 0,
        )
    )).scalar_one()
    rels = (await session.execute(select(func.count()).select_from(ChunkRelation))).scalar_one()
    anchors = (await session.execute(
        select(func.count()).select_from(AnchorMapping).where(AnchorMapping.is_active == True)  # noqa: E712
    )).scalar_one()
    last = (await session.execute(
        select(SyncTask).where(SyncTask.status == "COMPLETED")
        .order_by(SyncTask.completed_at.desc()).limit(1)
    )).scalar_one_or_none()
    return SyncStatusResponse(
        status="HEALTHY",
        last_sync_at=last.completed_at if last else None,
        last_commit=last.commit_hash if last else None,
        stats=SyncStats(total_chunks=code + doc, code_chunks=code, doc_chunks=doc,
                        stale_docs=stale, total_relations=rels, total_anchors=anchors),
    )


@router.get("/tasks", response_model=SyncTaskListResponse)
async def list_tasks(
    session: AsyncSession = Depends(get_db),
    type: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> SyncTaskListResponse:
    pg = pagination(page, page_size)
    q = select(SyncTask).order_by(SyncTask.created_at.desc())
    cq = select(func.count()).select_from(SyncTask)
    if status:
        q = q.where(SyncTask.status == status)
        cq = cq.where(SyncTask.status == status)
    if type:
        q = q.where(SyncTask.change_details["type"].astext == type)
        cq = cq.where(SyncTask.change_details["type"].astext == type)
    rows = (await session.execute(q.offset(pg["offset"]).limit(pg["page_size"]))).scalars().all()
    total = (await session.execute(cq)).scalar_one()

    rb_map: dict[str, RollbackHistory] = {}
    if rows:
        commits = [r.commit_hash for r in rows]
        rb_rows = (await session.execute(
            select(RollbackHistory).where(RollbackHistory.rollback_commit.in_(commits))
        )).scalars().all()
        rb_map = {r.rollback_commit: r for r in rb_rows}
    items = [_to_task_item(r, rb_map.get(r.commit_hash)) for r in rows]
    return SyncTaskListResponse(total=total, items=items)


@router.get("/tasks/{task_id}", response_model=SyncTaskDetailResponse)
async def get_task(task_id: int, session: AsyncSession = Depends(get_db)) -> SyncTaskDetailResponse:
    task = await session.get(SyncTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    cd = task.change_details or {}
    changes = [ChangeDetailItem(**c) for c in cd.get("changes", [])]
    rb = (await session.execute(
        select(RollbackHistory).where(RollbackHistory.rollback_commit == task.commit_hash).limit(1)
    )).scalar_one_or_none()
    base = _to_task_item(task, rb)
    return SyncTaskDetailResponse(
        **base.model_dump(), change_details=changes, errors=cd.get("errors", []),
    )


@router.get("/rollbacks", response_model=RollbackListResponse)
async def list_rollbacks(
    session: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> RollbackListResponse:
    pg = pagination(page, page_size)
    q = select(RollbackHistory).order_by(RollbackHistory.created_at.desc())
    rows = (await session.execute(q.offset(pg["offset"]).limit(pg["page_size"]))).scalars().all()
    total = (await session.execute(select(func.count()).select_from(RollbackHistory))).scalar_one()
    items = [RollbackItem(
        rollback_id=r.rollback_id, rollback_commit=r.rollback_commit, source_commit=r.source_commit,
        chunks_rolled_back=r.chunks_rolled_back, chunks_restored=r.chunks_restored,
        chunks_deleted=r.chunks_deleted, relations_restored=r.relations_restored,
        anchors_restored=r.anchors_restored, stale_anchors_cleared=r.stale_anchors_cleared,
        doc_pr_closed=r.doc_pr_closed, triggered_by=r.triggered_by, status=r.status,
        created_at=r.created_at,
    ) for r in rows]
    return RollbackListResponse(total=total, items=items)


@router.post("/trigger", response_model=TriggerResponse)
async def trigger_sync(body: TriggerRequest) -> TriggerResponse:
    """触发一次同步（同步执行：跑完返回 COMPLETED/FAILED 的任务）。"""
    engine = sync_service.get_sync_engine()
    task = await asyncio.to_thread(
        sync_service.run_sync_on_engine, engine, settings.repo_path, body.type, body.target_commit,
    )
    if task.status == "COMPLETED":
        msg = f"同步完成（{body.type}，{task.files_changed} 文件变更）"
    else:
        msg = f"同步失败：{task.error_message or ''}"
    return TriggerResponse(task_id=task.task_id, status=task.status, message=msg)


@router.get("/change-history/{chunk_id}", response_model=ChangeHistoryResponse)
async def change_history(
    chunk_id: str,
    session: AsyncSession = Depends(get_db),
    limit: int = Query(10, ge=1, le=100),
) -> ChangeHistoryResponse:
    rows = (await session.execute(
        select(ChangeHistory).where(ChangeHistory.chunk_id == chunk_id)
        .order_by(ChangeHistory.created_at.desc()).limit(limit)
    )).scalars().all()
    items = [ChangeHistoryItem(
        commit=r.git_commit_hash, change_type=r.change_type,
        date=r.git_commit_time or r.created_at, old_hash=r.old_content_hash,
        new_hash=r.new_content_hash, is_rollback_related=r.is_rollback_related,
    ) for r in rows]
    # change_history 无 file_path 列（风险 #8）——从 chunk_id 前缀尽力推导
    file_hint = None
    if chunk_id.startswith("code_") or chunk_id.startswith("doc_"):
        file_hint = None  # 无法可靠反推文件路径
    return ChangeHistoryResponse(chunk_id=chunk_id, file=file_hint, history=items)
