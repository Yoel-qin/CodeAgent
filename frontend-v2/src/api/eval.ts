/** V2-M8 评测 REST 客户端（/v1/eval/*）。 */
import { api } from "./client";

export interface VariantMetrics {
  n_cases: number;
  code_hit_rate: number | null;
  doc_hit_rate: number | null;
  citation_precision: number | null;
  rounds_mean: number | null;
  rounds_p95: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  tokens_mean: number | null;
}

export interface EvalJudgeScores {
  faithfulness: number;
  answer_relevance: number;
  citation_accuracy: number;
  hallucination: number;
}

export interface EvalVariantConfig {
  name: string;
  rounds_code?: number | null;
  rounds_doc?: number | null;
  code_no_graph?: boolean;
  model_reasoning?: string | null;
  top_k?: number | null;
}

export interface EvalRunSummary {
  id: number;
  repo: string;
  kind: "single" | "ab";
  status: "RUNNING" | "DONE" | "FAILED";
  config: { trigger?: string; judge?: boolean; variants?: EvalVariantConfig[] } | null;
  metrics: { variants: Record<string, VariantMetrics>; judge: EvalJudgeScores | null } | null;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface EvalQueryRow {
  case_id: string;
  variant: string;
  hit_code: boolean;
  hit_doc: boolean;
  has_code_anchor: boolean;
  has_doc_anchor: boolean;
  matched: number;
  total: number;
  precision: number | null;
  rounds: number;
  latency_ms: number;
  tokens: number | null;
  llm_calls: number | null;
  route: string;
  answer_chars: number;
  unresolved: string[];
}

export interface EvalRunDetail extends EvalRunSummary {
  per_query: EvalQueryRow[] | null;
}

export interface EvalRunList {
  total: number;
  items: EvalRunSummary[];
}

export const runEval = (body: {
  repo?: string;
  variants?: EvalVariantConfig[];
  judge?: boolean;
}) => api.post<EvalRunDetail>("/v1/eval/run", body, { timeout: 600_000 }).then((r) => r.data);

export const listEvalRuns = (limit = 50, offset = 0) =>
  api.get<EvalRunList>("/v1/eval/runs", { params: { limit, offset } }).then((r) => r.data);

export const getEvalRun = (id: number) =>
  api.get<EvalRunDetail>(`/v1/eval/runs/${id}`).then((r) => r.data);
