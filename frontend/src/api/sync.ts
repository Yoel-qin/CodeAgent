/** 同步管理模块 REST 客户端（对齐后端 api接口清单 §六 / schemas/sync.py）。 */
import { api } from "./client";

// ---- GET /sync/status ----

export interface SyncStats {
  total_chunks: number;
  code_chunks: number;
  doc_chunks: number;
  stale_docs: number;
  total_relations: number;
  total_anchors: number;
}

export interface SyncStatusResponse {
  status: string;
  last_sync_at: string | null;
  last_commit: string | null;
  stats: SyncStats;
}

// ---- GET /sync/tasks ----

export interface ChangeSummary {
  added: number;
  modified: number;
  deleted: number;
}

export interface RollbackDetail {
  chunks_rolled_back: number;
  chunks_restored: number;
  relations_restored: number;
  anchors_restored: number;
  stale_anchors_cleared: number;
}

export interface SyncTaskItem {
  task_id: number;
  type: string;
  commit: string;
  status: string;
  changes: ChangeSummary;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  doc_pr_url: string | null;
  doc_pr_status: string | null;
  source_commit: string | null;
  rollback_detail: RollbackDetail | null;
  triggered_by: string | null;
}

export interface SyncTaskListResponse {
  total: number;
  items: SyncTaskItem[];
}

export interface ChangeDetailItem {
  chunk_id: string;
  file: string | null;
  change_type: string;
  rollback_source_commit: string | null;
}

export interface SyncTaskDetailResponse extends SyncTaskItem {
  change_details: ChangeDetailItem[];
  errors: Array<{ file?: string; change?: string; error: string }>;
}

// ---- GET /sync/rollbacks ----

export interface RollbackItem {
  rollback_id: number;
  rollback_commit: string;
  source_commit: string;
  chunks_rolled_back: number;
  chunks_restored: number;
  chunks_deleted: number;
  relations_restored: number;
  anchors_restored: number;
  stale_anchors_cleared: number;
  doc_pr_closed: string | null;
  triggered_by: string;
  status: string;
  created_at: string;
}

export interface RollbackListResponse {
  total: number;
  items: RollbackItem[];
}

// ---- POST /sync/trigger ----

export type SyncType = "FULL" | "INCREMENTAL";

export interface TriggerRequest {
  type: SyncType;
  target_commit?: string | null;
}

export interface TriggerResponse {
  task_id: number;
  status: string;
  message: string;
}

// ---- GET /sync/change-history/{chunk_id} ----

export interface ChangeHistoryItem {
  commit: string;
  change_type: string;
  date: string | null;
  old_hash: string | null;
  new_hash: string | null;
  is_rollback_related: boolean;
}

export interface ChangeHistoryResponse {
  chunk_id: string;
  file: string | null;
  history: ChangeHistoryItem[];
}

// ---- 调用 ----

export const getSyncStatus = () =>
  api.get<SyncStatusResponse>("/v1/sync/status").then((r) => r.data);

export const listSyncTasks = (params: { type?: string; status?: string; page?: number; page_size?: number } = {}) =>
  api.get<SyncTaskListResponse>("/v1/sync/tasks", { params }).then((r) => r.data);

export const getSyncTask = (taskId: number) =>
  api.get<SyncTaskDetailResponse>(`/v1/sync/tasks/${taskId}`).then((r) => r.data);

export const listRollbacks = (params: { page?: number; page_size?: number } = {}) =>
  api.get<RollbackListResponse>("/v1/sync/rollbacks", { params }).then((r) => r.data);

export const triggerSync = (body: TriggerRequest) =>
  api.post<TriggerResponse>("/v1/sync/trigger", body).then((r) => r.data);

export const getChangeHistory = (chunkId: string) =>
  api.get<ChangeHistoryResponse>(`/v1/sync/change-history/${chunkId}`).then((r) => r.data);
