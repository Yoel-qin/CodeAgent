/** 检索评测模块 REST 客户端（对齐后端 /v1/eval/* + schemas/eval.py，M27）。 */
import { api } from "./client";

// aggregate 的 recall/precision/ndcg 按 K，K 经 JSON 序列化为字符串 key（按 ["10"] 索引）。
export interface EvalAggregate {
  n: number;
  recall: Record<string, number | null>;
  precision: Record<string, number | null>;
  mrr: number | null;
  ndcg: Record<string, number | null>;
}

export interface EvalRunRequest {
  top_k?: number;
  rewrite?: "off" | "auto";
  eval_set?: string | null;
  persist?: boolean;
  ablation?: Record<string, boolean>; // M29: {"rerank": false} 跑单变体；省略=全开=生产
}

export interface EvalRunSummary {
  run_id: number;
  status: string;
  trigger: string;
  top_k: number;
  rewrite: string;
  embedding_strategy: string;
  n_queries: number;
  n_evaluable: number;
  rerank_on_count: number;
  duration_ms: number | null;
  unresolved_count: number;
  aggregate: EvalAggregate | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  error_message: string | null;
  ablation?: Record<string, boolean> | null; // M29: 单次评测变体配置（如 {"rerank": false}）；null=全开
}

export interface PerQueryRow {
  id: string;
  text: string;
  relevant?: string[];
  retrieved?: string[];
  missing?: string[];
  rerank_on: boolean;
  error: string | null;
  recall?: Record<string, number>;
  precision?: Record<string, number>;
  mrr?: number;
  ndcg?: Record<string, number>;
  first_hit_rank?: number | null;
  [k: string]: unknown;
}

export interface EvalRunDetail extends EvalRunSummary {
  config: Record<string, unknown> | null;
  per_query: PerQueryRow[] | null;
  unresolved: Array<Record<string, unknown>> | null;
}

export interface EvalRunListResponse {
  total: number;
  items: EvalRunSummary[];
}

// ===== A/B 消融（M28；对齐后端 schemas/eval.py AB* 模型）=====

export interface ABDelta {
  abs: number | null;
  pct: number | null;
}

export interface ABPairResult {
  name: string;
  claim: string;
  baseline: string;
  treatment: string;
  metric_focus: string[];
  // delta: { recall: {"10": {abs,pct}}, mrr: {abs,pct}, ... }
  delta: Record<string, Record<string, ABDelta> | ABDelta>;
}

export interface ABVariantResult {
  ablation: Record<string, boolean>;
  desc: string;
  aggregate: EvalAggregate | null;
  n_evaluable: number;
  n_queries: number;
  rerank_on_count: number;
  unresolved: number;
  per_query?: PerQueryRow[] | null;
}

export interface ABRunRequest {
  top_k?: number;
  rewrite?: "off" | "auto";
  eval_set?: string | null;
  pairs?: string[] | null; // ["rerank","multipath_rrf","graph"]；缺省=默认 3 组
  graph_subset?: boolean;
  diagnose?: boolean;
  persist?: boolean;
}

export interface ABRunSummary {
  run_id: number;
  status: string;
  trigger: string;
  top_k: number;
  rewrite: string;
  embedding_strategy: string;
  n_queries: number;
  n_evaluable: number;
  rerank_on_count: number;
  duration_ms: number | null;
  pairs: ABPairResult[];
  aggregate: EvalAggregate | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  error_message: string | null;
  kind: "ab";
}

export interface ABRunDetail extends ABRunSummary {
  variants: Record<string, ABVariantResult>;
  config: Record<string, unknown> | null;
}

export interface ABRunListResponse {
  total: number;
  items: ABRunSummary[];
}

// 评测 ~85 query × 全漏斗（含重排 API）≈ 数十秒，超过 client.ts 默认 30s 超时 → 单请求覆盖到 300s。
export const runEval = (body: EvalRunRequest) =>
  api.post<EvalRunDetail>("/v1/eval/run", body, { timeout: 300_000 }).then((r) => r.data);

export const listEvalRuns = (limit = 50, kind?: "single" | "ab") =>
  api
    .get<EvalRunListResponse>("/v1/eval/runs", { params: { limit, ...(kind ? { kind } : {}) } })
    .then((r) => r.data);

export const getEvalRun = (id: number) =>
  api.get<EvalRunDetail>(`/v1/eval/runs/${id}`).then((r) => r.data);

// A/B 跑 3~4 变体 × ~85 query × 全漏斗，比单次更慢 → 600s 超时。
export const runAb = (body: ABRunRequest) =>
  api.post<ABRunDetail>("/v1/eval/ab", body, { timeout: 600_000 }).then((r) => r.data);

export const listAbRuns = (limit = 50) =>
  api.get<ABRunListResponse>("/v1/eval/ab-runs", { params: { limit } }).then((r) => r.data);

export const getAbRun = (id: number, diagnose = false) =>
  api.get<ABRunDetail>(`/v1/eval/ab-runs/${id}`, { params: { diagnose } }).then((r) => r.data);
