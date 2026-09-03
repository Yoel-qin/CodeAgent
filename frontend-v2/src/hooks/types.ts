/** v2 SSE/持久化契约公共类型（Plan 4 冻结形状）。 */
export interface Citation {
  kind: "code" | "doc";
  label: string;
  file_path?: string;
  start_line?: number;
  end_line?: number;
  doc_id?: string;
  section?: string;
}

export interface AgentStep {
  tool: string;
  args: Record<string, unknown>;
  n: number;
  duration_ms?: number | null;
}

export interface RetrievalInfo {
  mode: "codenav" | "docqa" | "retrieve" | "clarify";
  intent?: string;
  confidence?: number;
  code_hits?: number;
  doc_hits?: number;
  tools?: string[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  retrieval?: RetrievalInfo;
  agentSteps?: AgentStep[];
  route?: string;
  streaming?: boolean;
  error?: boolean;
  createdAt: number;
  messageId?: number; // 服务端 chat_messages.id（done 事件）
  feedback?: "HELPFUL" | "NOT_HELPFUL";
}
