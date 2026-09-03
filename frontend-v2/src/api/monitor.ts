/** v2 监控 REST 客户端（/v1/monitor/*）。 */
import { api } from "./client";

export type MonitorWindow = "today" | "7d" | "all";

export interface Overview {
  window: string;
  requests: number;
  avg_duration_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  avg_tool_calls: number | null;
  avg_tokens: number | null;
  codenav_hit_rate: number | null;
  routes: Record<string, number>;
}

export interface TraceTokens {
  spent_tokens: number | null;
  llm_calls: number | null;
  estimated: boolean | null;
}

export interface TraceListItem {
  message_id: number;
  query: string;
  route: string;
  total_ms: number | null;
  tokens: TraceTokens | null;
  n_tool_calls: number;
  created_at: string | null;
}

export interface TraceList {
  window: string;
  total: number;
  items: TraceListItem[];
}

export interface TraceSpan {
  span_id: number;
  parent_id: number | null;
  kind: string;
  name: string;
  start_ms: number;
  duration_ms: number | null;
  status: string;
  error: string | null;
  tokens: { prompt: number; completion: number; estimated: boolean } | null;
  attrs: Record<string, unknown>;
}

export interface TraceDetail {
  message_id: number;
  conversation_id: string;
  query: string;
  route: string;
  legacy: boolean;
  spans: TraceSpan[];
  summary: { total_ms: number; tokens?: TraceTokens; n_spans: number } | null;
  created_at: string | null;
}

export interface PipelineStats {
  stream: { length: number; pending: number; lag: number | null; group: string } | null;
  dead: { length: number } | null;
  events: Record<string, number> | null;
  last_event_at: string | null;
}

export const getOverview = (win: MonitorWindow = "7d") =>
  api.get<Overview>("/v1/monitor/overview", { params: { window: win } }).then((r) => r.data);

export const listTraces = (win: MonitorWindow = "7d", limit = 50) =>
  api.get<TraceList>("/v1/monitor/traces", { params: { window: win, limit } }).then((r) => r.data);

export const getTrace = (messageId: number) =>
  api.get<TraceDetail>(`/v1/monitor/traces/${messageId}`).then((r) => r.data);

export const getPipelineStats = () =>
  api.get<PipelineStats>("/v1/monitor/pipeline").then((r) => r.data);
