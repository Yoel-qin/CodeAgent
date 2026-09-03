/** 会话/检索详情/追问/反馈 REST 客户端（对齐后端 §2.2–2.6）。 */
import { api } from "./client";

export interface ConversationItem {
  conversation_id: string;
  title: string;
  agent_type: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationListResponse {
  total: number;
  items: ConversationItem[];
}

export interface HistoryMessage {
  message_id: string;
  role: "user" | "assistant";
  content: string;
  citations: CitationBrief[] | null;
  agent_type: string | null;
  created_at: string;
}

export interface CitationBrief {
  type: "code" | "doc";
  chunk_id: string;
  label?: string | null;
  class?: string | null;
  method?: string | null;
  path?: string[] | null;
  score?: number;
}

export interface ConversationDetail {
  conversation_id: string;
  title: string;
  agent_type: string | null;
  messages: HistoryMessage[];
}

export interface RetrievalChannel {
  name: string;
  count: number;
}

/** Agent 工具调用轨迹一步（对齐后端 agent_step 事件 / RetrievalDetailResponse.agent）。 */
export interface AgentStepBrief {
  tool: string;
  args: Record<string, unknown>;
  n: number;
}

export interface AgentTrace {
  type: string; // agent_type，如 "CHANGE_IMPACT"
  steps: AgentStepBrief[];
}

export interface RetrievalDetail {
  stage1: {
    latency_ms: number | null;
    channels: RetrievalChannel[];
    merged_count: number;
    terms: string[];
  };
  stage2: { model: string | null; latency_ms: number | null; output_count: number | null };
  stage3: {
    model: string;
    latency_ms: number | null;
    output_count: number;
    rerank_on: boolean;
    results: CitationBrief[];
  };
  // 仅 RAG_ENGINE=langgraph 的场景 Agent 消息才有（legacy/retrieve 为 undefined）
  agent?: AgentTrace;
}

export const listConversations = (params: { page?: number; page_size?: number; agent_type?: string } = {}) =>
  api.get<ConversationListResponse>("/v1/chat/conversations", { params }).then((r) => r.data);

export const getConversation = (id: string) =>
  api.get<ConversationDetail>(`/v1/chat/conversations/${id}`).then((r) => r.data);

export const getRetrieval = (messageId: string) =>
  api.get<RetrievalDetail>(`/v1/chat/messages/${messageId}/retrieval`).then((r) => r.data);

export const FEEDBACK_CATEGORIES = [
  "答案错误", "引用不符", "检索遗漏", "答非所问", "内容编造", "其他",
] as const;

export const postFeedback = (
  messageId: string,
  rating: "HELPFUL" | "NOT_HELPFUL",
  categories?: string[],
  correction?: string,
) =>
  api
    .post(`/v1/chat/messages/${messageId}/feedback`, { rating, categories, correction })
    .then((r) => r.data);

export interface FeedbackReport {
  summary: { total: number; negative: number; negative_rate: number };
  categories: { category: string; count: number }[];
  by_repo: { repo: string; count: number }[];
  keywords: string[];
  hallucination_alerts: {
    log_id: number;
    query: string;
    correction: string;
    enforcement_ratio: number | null;
    repo: string | null;
  }[];
}

export const getFeedbackReport = (days = 30) =>
  api.get<FeedbackReport>("/v1/monitor/feedback-report", { params: { days } }).then((r) => r.data);

export const postSuggestions = (conversationId: string, lastMessageId: string) =>
  api
    .post<{ suggestions: string[] }>("/v1/chat/suggestions", {
      conversation_id: conversationId,
      last_message_id: lastMessageId,
    })
    .then((r) => r.data.suggestions);
