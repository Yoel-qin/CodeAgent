/** Agent 面板 REST 客户端（对齐后端 api接口清单 §七 / schemas/agent.py）。 */
import { api } from "./client";

// ---- GET /agents/stats ----

export interface AgentStatRow {
  agent: string;
  calls: number;
  avg_steps: number | null;
  hit_rate: number | null; // 产出引用的 run 占比（0~1）
  satisfaction: number | null; // HELPFUL / 已反馈（0~1）
  degraded: number;
}

export interface AgentStats {
  window: string; // today / 7d / all
  total_calls: number; // Agent 成功 run（mode='agent'）
  engaged: number; // 降级率分母
  degraded: number;
  degradation_rate: number | null;
  avg_steps: number | null;
  helpful: number;
  feedback: number;
  satisfaction: number | null;
  per_agent: AgentStatRow[];
}

// ---- GET /agents/runs ----

export interface AgentRunItem {
  log_id: number;
  created_at: string;
  agent: string | null; // 降级 run 经 chat_messages.agent_type 回退，仍可能为 null
  query: string;
  steps: number;
  citations: number;
  degraded: boolean;
  feedback: string | null;
}

export interface AgentRunsResponse {
  total: number;
  items: AgentRunItem[];
}

export type AgentWindow = "today" | "7d" | "all";

// ---- 调用 ----

export const getAgentStats = (win: AgentWindow = "today") =>
  api.get<AgentStats>("/v1/agents/stats", { params: { window: win } }).then((r) => r.data);

export const listAgentRuns = (params: { page?: number; page_size?: number } = {}) =>
  api.get<AgentRunsResponse>("/v1/agents/runs", { params }).then((r) => r.data);
