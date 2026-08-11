/** 系统监控 REST 客户端（对齐后端 Phase 8.4 / schemas/monitor.py）。 */
import { api } from "./client";

export type MonitorWindow = "today" | "7d" | "all";

// ---- GET /monitor/retrieval-perf ----

export interface LatencyMs {
  avg_total: number | null;
  p50_total: number | null;
  p95_total: number | null;
  avg_recall: number | null;
  avg_rerank: number | null;
}

export interface RetrievalFunnel {
  avg_pool: number | null; // RRF 融合池均值
  avg_final: number | null; // 精排后候选均值
}

export interface FeedbackCounts {
  helpful: number;
  not_helpful: number;
}

export interface RetrievalPerf {
  window: string;
  queries: number;
  latency_ms: LatencyMs;
  funnel: RetrievalFunnel;
  rerank_rate: number | null; // 启用精排占比（0~1）
  feedback: FeedbackCounts;
}

// ---- GET /monitor/resources ----

export interface ComponentInfo {
  up: boolean | null;
  detail?: string | null;
  db_size_bytes?: number | null; // postgres
  used_memory_bytes?: number | null; // redis
  keys?: number | null; // redis
  collections?: number | null; // milvus
  rows?: number | null; // milvus
  doc_count?: number | null; // elasticsearch
  size_bytes?: number | null; // elasticsearch store
  asset_bytes?: number | null; // minio
}

export interface Resources {
  status: "healthy" | "degraded";
  components: Record<string, ComponentInfo>;
}

// ---- GET /monitor/api-usage ----

export interface ApiUsage {
  window: string;
  llm_calls: number;
  embedding_query_calls: number;
  rerank_calls: number;
  generated_tokens_est: number;
  indexed_tokens: number;
  note: string;
}

// ---- GET /monitor/index-stats ----

export interface MilvusCollectionStat {
  name: string;
  dim: number | null;
  rows: number | null;
}

export interface PostgresIndexStats {
  code_chunks: number;
  code_chunks_active: number;
  code_chunks_synced_pct: number | null;
  doc_chunks: number;
  doc_chunks_active: number;
  doc_chunks_synced_pct: number | null;
  chunk_relations: number;
  chunk_relations_stale: number;
  call_graph: number;
  call_graph_active: number;
  code_files: number;
  doc_files: number;
  doc_resources: number;
  retrieval_logs: number;
  conversations: number;
  chat_messages: number;
}

export interface MilvusIndexStats {
  strategy: string;
  collections: MilvusCollectionStat[];
}

export interface EsIndexStats {
  index: string;
  doc_count: number | null;
  by_kind: Record<string, number | null>;
}

export interface IndexStats {
  postgres: PostgresIndexStats;
  milvus: MilvusIndexStats;
  elasticsearch: EsIndexStats;
}

// ---- 调用 ----

export const getRetrievalPerf = (win: MonitorWindow = "today") =>
  api.get<RetrievalPerf>("/v1/monitor/retrieval-perf", { params: { window: win } }).then((r) => r.data);

export const getApiUsage = (win: MonitorWindow = "today") =>
  api.get<ApiUsage>("/v1/monitor/api-usage", { params: { window: win } }).then((r) => r.data);

export const getIndexStats = () =>
  api.get<IndexStats>("/v1/monitor/index-stats").then((r) => r.data);

export const getResources = () =>
  api.get<Resources>("/v1/monitor/resources").then((r) => r.data);
