/** 主动腐化巡检 / 提案审批模块 REST 客户端（对齐后端 api/v1/staleness.py / schemas/staleness.py）。
 * M16 报告 / M17 SWEEP 批量重写 / M18 审批写回 / M19 审批 UI。 */
import { api } from "./client";

// ---- GET /staleness/report ----

export interface StalenessFinding {
  relation_id: number;
  relation_type: string | null;
  anchor_key: string | null;
  stale_reason: string | null;
  updated_at: string | null;
}

export interface StalenessSourceBreakdown {
  sweep: number; // 巡检自动标记（stale_reason LIKE 'SWEEP:%'）
  deleted: number; // soft_delete 代码删除标记（'DELETED:%'）
  other: number; // HITL 审批 / 其他来源
}

export interface StalenessReport {
  total: number;
  stale: number;
  by_source: StalenessSourceBreakdown;
  recent: StalenessFinding[];
}

// ---- GET /staleness/proposals ----

/** 提案状态：active（占位）= PENDING_PUSH / PENDING_MANUAL；其余为已决定 / 失败态。 */
export type ProposalStatus =
  | "PENDING_PUSH"
  | "PENDING_MANUAL"
  | "FAILED"
  | "MERGED"
  | "CLOSED_BY_ROLLBACK"
  | "APPROVED"
  | "REJECTED";

export interface ProposalItem {
  proposal_id: number | null;
  conversation_id: string | null;
  file_id: number | null;
  doc_chunk_id: string | null;
  heading_path: string[];
  relation_ids: number[];
  status: string | null;
  rewritten_ok: boolean;
  artifact_key: string | null;
  branch_name: string | null;
  commit_sha: string | null; // M21 真 git 产出的分支提交
  pr_url: string | null; // M21 推送目标 URL
  /** M19 审批 UI 预览：list 端点填充；sweep-rewrite 项缺省 null。 */
  rewritten_text: string | null;
  original_text: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProposalListResponse {
  total: number;
  items: ProposalItem[];
}

// ---- POST /staleness/sweep-rewrite ----

export interface SweepRewriteRequest {
  top_n: number;
}

export interface SweepRewriteResponse {
  scanned: number; // 池中命中的 SWEEP 过时关系数
  slots: number; // 按 doc_chunk_id 去重后的文档段落数
  skipped_existing: number; // 已有 active 提案（幂等守卫）跳过数
  rewritten: number; // LLM 重写成功数
  pending_push: number;
  pending_manual: number;
  failed: number;
  proposals: ProposalItem[];
  error: string | null;
}

// ---- POST /staleness/proposals/{id}/decide ----

export type ProposalDecision = "APPROVED" | "REJECTED";

export interface ProposalDecisionResponse {
  proposal_id: number;
  status: string;
  applied: boolean; // APPROVED 且成功写回 doc_chunks 才为 true
  doc_chunk_id: string | null;
  relations_cleared: number; // 清掉过时标记的锚点关系数
  reembed_status?: "synced" | "failed" | "lazy" | null; // M20 eager 重嵌入结果（REJECTED/未apply=null）
  git_status?: "PUSHED" | "COMMITTED" | "PUSH_FAILED" | null; // M21 真 git 结果（REJECTED/未apply/disabled=null）
  commit_sha?: string | null; // M21 分支提交 sha
  pr_url?: string | null; // M21 推送目标 URL
}

// ---- 调用 ----

export const getStalenessReport = (recent = 20) =>
  api.get<StalenessReport>("/v1/staleness/report", { params: { recent } }).then((r) => r.data);

export const listStalenessProposals = (
  params: { status?: string; page?: number; page_size?: number } = {},
) => api.get<ProposalListResponse>("/v1/staleness/proposals", { params }).then((r) => r.data);

export const runSweepRewrite = (body: SweepRewriteRequest) =>
  api.post<SweepRewriteResponse>("/v1/staleness/sweep-rewrite", body).then((r) => r.data);

export const decideProposal = (proposalId: number, decision: ProposalDecision) =>
  api
    .post<ProposalDecisionResponse>(`/v1/staleness/proposals/${proposalId}/decide`, { decision })
    .then((r) => r.data);
