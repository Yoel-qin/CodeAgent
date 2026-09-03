/** v2 同步管道 REST 客户端（/v1/sync/events + webhook 手动触发）。 */
import { api } from "./client";

export interface PipelineEventItem {
  id: number;
  repo: string;
  commit_hash: string;
  path: string;
  event_kind: string; // file | graph_rebuild
  status: string; // PENDING | DONE | DEAD
  attempts: number;
  last_error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export const listSyncEvents = (
  params: { repo?: string; status?: string; limit?: number; offset?: number } = {},
) =>
  api
    .get<{ total: number; items: PipelineEventItem[] }>("/v1/sync/events", { params })
    .then((r) => r.data);

export const sendWebhook = (body: {
  repo: string;
  commit_hash: string;
  files: { path: string; status: string }[];
}) =>
  api
    .post<{ enqueued: boolean; event_id: string }>("/v1/sync/webhook", body)
    .then((r) => r.data);
