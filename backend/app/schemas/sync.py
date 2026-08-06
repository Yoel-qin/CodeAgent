"""同步管理模块的请求/响应 schema（对齐 api接口清单 §六）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# ---- GET /status ----

class SyncStats(BaseModel):
    total_chunks: int
    code_chunks: int
    doc_chunks: int
    stale_docs: int
    total_relations: int
    total_anchors: int


class SyncStatusResponse(BaseModel):
    status: str
    last_sync_at: datetime | None = None
    last_commit: str | None = None
    stats: SyncStats


# ---- GET /tasks ----

class ChangeSummary(BaseModel):
    added: int
    modified: int
    deleted: int


class RollbackDetail(BaseModel):
    chunks_rolled_back: int
    chunks_restored: int
    relations_restored: int
    anchors_restored: int
    stale_anchors_cleared: int


class SyncTaskItem(BaseModel):
    task_id: int
    type: str
    commit: str
    status: str
    changes: ChangeSummary
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    doc_pr_url: str | None = None
    doc_pr_status: str | None = None
    source_commit: str | None = None
    rollback_detail: RollbackDetail | None = None
    triggered_by: str | None = None


class SyncTaskListResponse(BaseModel):
    total: int
    items: list[SyncTaskItem]


class ChangeDetailItem(BaseModel):
    chunk_id: str
    file: str | None = None
    change_type: str
    rollback_source_commit: str | None = None


class SyncTaskDetailResponse(SyncTaskItem):
    change_details: list[ChangeDetailItem] = []
    errors: list[dict] = []


# ---- GET /rollbacks ----

class RollbackItem(BaseModel):
    rollback_id: int
    rollback_commit: str
    source_commit: str
    chunks_rolled_back: int
    chunks_restored: int
    chunks_deleted: int
    relations_restored: int
    anchors_restored: int
    stale_anchors_cleared: int
    doc_pr_closed: str | None = None
    triggered_by: str
    status: str
    created_at: datetime


class RollbackListResponse(BaseModel):
    total: int
    items: list[RollbackItem]


# ---- POST /trigger ----

class TriggerRequest(BaseModel):
    type: Literal["FULL", "INCREMENTAL"]
    target_commit: str | None = None


class TriggerResponse(BaseModel):
    task_id: int
    status: str
    message: str


# ---- GET /change-history/{chunk_id} ----

class ChangeHistoryItem(BaseModel):
    commit: str
    change_type: str
    date: datetime | None = None
    old_hash: str | None = None
    new_hash: str | None = None
    is_rollback_related: bool = False


class ChangeHistoryResponse(BaseModel):
    chunk_id: str
    file: str | None = None
    history: list[ChangeHistoryItem]
