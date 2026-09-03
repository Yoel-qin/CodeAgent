/** 会话与反馈 REST 客户端（v2：GET /v1/chat/conversations 裸数组 + meta 携带 citations/agent_steps）。 */
import { api } from "./client";
import type { AgentStep, Citation } from "../hooks/types";

export interface ConversationItem {
  id: string;
  title: string;
  target_repo: string;
  created_at: string;
  updated_at: string;
}

export interface HistoryMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  meta: { citations?: Citation[]; agent_steps?: AgentStep[]; intent?: string; route?: string } | null;
  created_at: string;
}

export interface ConversationDetail {
  conversation: ConversationItem;
  messages: HistoryMessage[];
}

export const listConversations = () =>
  api.get<ConversationItem[]>("/v1/chat/conversations").then((r) => r.data);

export const getConversation = (id: string) =>
  api.get<ConversationDetail>(`/v1/chat/conversations/${id}`).then((r) => r.data);

export const postFeedback = (messageId: number, rating: "HELPFUL" | "NOT_HELPFUL", comment?: string) =>
  api.post(`/v1/chat/messages/${messageId}/feedback`, { rating, comment }).then((r) => r.data);
