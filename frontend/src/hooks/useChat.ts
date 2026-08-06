import { useCallback, useRef, useState } from "react";
import { streamChat, streamResume } from "../api/sse";
import { getConversation } from "../api/conversations";

export interface Citation {
  type: "code" | "doc";
  chunk_id: string;
  label: string;
  class?: string | null;
  method?: string | null;
  path?: string[] | null;
  score?: number;
  content_type?: string; // Phase 1.5e: text | image | table | table_fragment
}

export interface RetrievalInfo {
  terms?: string[];
  // 旧字段（仍由后端返回，向后兼容）
  vector?: number;
  lexical?: number;
  graph?: number;
  merged?: number;
  // 检索漏斗（Phase 2 精排 + RRF）
  recall?: { vector?: number; lexical?: number; graph?: number };
  bm25?: boolean;
  vector_on?: boolean;
  rrf_pool?: number;
  coarse?: number | null;
  fine?: number;
  rerank_on?: boolean;
  recall_ms?: number;
  rerank_ms?: number;
  // 场景 Agent 路径（mode:agent）：漏斗全零，真实信息在 agentSteps
  mode?: string;
  agent?: string;
}

/** Agent 工具调用轨迹一步（agent_step 事件，M5 可观测性）。 */
export interface AgentStep {
  tool: string;
  args: Record<string, unknown>;
  n: number;
}

export type Feedback = "HELPFUL" | "NOT_HELPFUL";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  retrieval?: RetrievalInfo;
  agent?: string;
  agentSteps?: AgentStep[]; // 实时累积的 Agent 工具调用轨迹（仅 langgraph 场景 Agent 流式消息）
  streaming?: boolean;
  error?: boolean;
  createdAt: number;
  messageId?: string; // 服务端消息 ID（assistant 消息，来自 done 事件）
  feedback?: Feedback;
  interrupt?: { proposal: string; awaiting: boolean }; // HITL（M10）：图暂停待人工确认
}

let _seq = 0;
const uid = () => `m${Date.now()}_${_seq++}`;

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversationTitle, setConversationTitle] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);

  const patch = (id: string, fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((arr) => arr.map((m) => (m.id === id ? fn(m) : m)));

  const send = useCallback(
    async (query: string, agent: string) => {
      const q = query.trim();
      if (!q || streaming) return;
      const userMsg: ChatMessage = {
        id: uid(), role: "user", content: q, citations: [], agent, createdAt: Date.now(),
      };
      const aiId = uid();
      const aiMsg: ChatMessage = {
        id: aiId, role: "assistant", content: "", citations: [], streaming: true, createdAt: Date.now(),
      };
      setMessages((m) => [...m, userMsg, aiMsg]);
      setStreaming(true);

      const ac = new AbortController();
      ctrl.current = ac;
      try {
        await streamChat(
          { query: q, agent_type: agent, top_k: 8, conversation_id: conversationId ?? undefined },
          {
            onConversation: (info) => {
              const c = info as { conversation_id?: string; title?: string };
              if (c.conversation_id) setConversationId(c.conversation_id);
              if (c.title) setConversationTitle(c.title);
            },
            onRetrieval: (r) => patch(aiId, (m) => ({ ...m, retrieval: r as RetrievalInfo })),
            onCitation: (c) => patch(aiId, (m) => ({ ...m, citations: [...m.citations, c as Citation] })),
            onAgentStep: (s) =>
              patch(aiId, (m) => ({ ...m, agentSteps: [...(m.agentSteps ?? []), s as AgentStep] })),
            onToken: (t) => patch(aiId, (m) => ({ ...m, content: m.content + t })),
            onInterrupt: (info) => {
              // HITL（M10）：图暂停。记下 proposal + 服务端 message_id，弹审批框（streaming 关闭）
              const d = info as { proposal?: string; message_id?: string };
              patch(aiId, (m) => ({
                ...m,
                streaming: false,
                messageId: d.message_id ?? m.messageId,
                interrupt: { proposal: d.proposal ?? "", awaiting: true },
              }));
            },
            onDone: (meta) => {
              const d = meta as { message_id?: string };
              patch(aiId, (m) => ({ ...m, streaming: false, messageId: d.message_id ?? m.messageId }));
            },
            onError: () =>
              patch(aiId, (m) => ({
                ...m,
                streaming: false,
                error: true,
                content: m.content || "[连接失败：后端未响应，请确认后端已启动]",
              })),
          },
          ac.signal,
        );
      } finally {
        setStreaming(false);
        patch(aiId, (m) => ({ ...m, streaming: false }));
        ctrl.current = null;
      }
    },
    [streaming, conversationId],
  );

  /** 加载历史会话。 */
  const loadConversation = useCallback(async (id: string) => {
    if (streaming) return;
    const detail = await getConversation(id);
    setConversationId(detail.conversation_id);
    setConversationTitle(detail.title);
    setMessages(
      detail.messages.map((m) => ({
        id: m.message_id,
        role: m.role,
        content: m.content,
        citations: (m.citations ?? []) as Citation[],
        agent: m.agent_type ?? undefined,
        createdAt: new Date(m.created_at).getTime(),
        messageId: m.message_id,
      })),
    );
  }, [streaming]);

  /** 新建会话（清空当前）。 */
  const newConversation = useCallback(() => {
    if (streaming) return;
    setConversationId(null);
    setConversationTitle(null);
    setMessages([]);
  }, [streaming]);

  /** 记录消息反馈（本地态；持久化由调用方调 API）。 */
  const setFeedback = useCallback((messageId: string, fb: Feedback) => {
    setMessages((arr) => arr.map((m) => (m.messageId === messageId ? { ...m, feedback: fb } : m)));
  }, []);

  const stop = useCallback(() => {
    ctrl.current?.abort();
  }, []);

  const clear = useCallback(() => setMessages([]), []);

  /** HITL（M10）：对「等待人工确认」的消息给出决策，POST /v1/chat/resume 续跑图，token 流回同一条消息。 */
  const resume = useCallback(
    async (approved: boolean, comment?: string) => {
      if (streaming) return;
      const target = messages.find((m) => m.interrupt?.awaiting);
      if (!target?.messageId || !conversationId) return;
      const ac = new AbortController();
      ctrl.current = ac;
      setStreaming(true);
      patch(target.id, (m) => ({
        ...m,
        streaming: true,
        content: "",
        interrupt: m.interrupt ? { ...m.interrupt, awaiting: false } : m.interrupt,
      }));
      try {
        await streamResume(
          { conversation_id: conversationId, message_id: target.messageId, approved, comment: comment ?? null },
          {
            onToken: (t) => patch(target.id, (m) => ({ ...m, content: m.content + t })),
            onDone: (meta) => {
              const d = meta as { message_id?: string };
              patch(target.id, (m) => ({ ...m, streaming: false, messageId: d.message_id ?? m.messageId }));
            },
            onError: () =>
              patch(target.id, (m) => ({ ...m, streaming: false, error: true })),
          },
          ac.signal,
        );
      } finally {
        setStreaming(false);
        patch(target.id, (m) => ({ ...m, streaming: false }));
        ctrl.current = null;
      }
    },
    [streaming, messages, conversationId],
  );

  return {
    messages, streaming, send, resume, stop, clear,
    conversationId, conversationTitle,
    loadConversation, newConversation, setFeedback,
  };
}
