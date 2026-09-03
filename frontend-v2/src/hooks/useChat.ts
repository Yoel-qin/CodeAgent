import { useCallback, useRef, useState } from "react";
import { streamChat } from "../api/sse";
import { getConversation } from "../api/conversations";
import type { AgentStep, ChatMessage, Citation, RetrievalInfo } from "./types";

let _seq = 0;
const uid = () => `m${Date.now()}_${_seq++}`;

export function useChat(repo: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversationTitle, setConversationTitle] = useState<string | null>(null);
  const [conversationRepo, setConversationRepo] = useState<string>(repo);
  const ctrl = useRef<AbortController | null>(null);

  const patch = (id: string, fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((arr) => arr.map((m) => (m.id === id ? fn(m) : m)));

  const send = useCallback(async (query: string) => {
    const q = query.trim();
    if (!q || streaming) return;
    const aiId = uid();
    setMessages((m) => [...m,
      { id: aiId, role: "user", content: q, citations: [], createdAt: Date.now() },
      { id: `a${aiId}`, role: "assistant", content: "", citations: [], streaming: true, createdAt: Date.now() }]);
    setStreaming(true);
    const ac = new AbortController();
    ctrl.current = ac;
    const target = `a${aiId}`;
    try {
      await streamChat(
        { query: q, top_k: 8, conversation_id: conversationId ?? undefined, repo: conversationRepo || undefined },
        {
          onConversation: (info) => {
            const c = info as { conversation_id?: string; title?: string };
            if (c.conversation_id) setConversationId(c.conversation_id);
            if (c.title) setConversationTitle(c.title);
          },
          onRetrieval: (r) => patch(target, (m) => ({ ...m, retrieval: r as RetrievalInfo })),
          onCitation: (c) => patch(target, (m) => ({ ...m, citations: [...m.citations, c as Citation] })),
          onAgentStep: (s) => patch(target, (m) => ({ ...m, agentSteps: [...(m.agentSteps ?? []), s as AgentStep] })),
          onToken: (t) => patch(target, (m) => ({ ...m, content: m.content + t })),
          onDone: (meta) => {
            const d = meta as { message_id?: number };
            patch(target, (m) => ({ ...m, streaming: false, messageId: d.message_id ?? m.messageId }));
          },
          onError: () => patch(target, (m) => ({
            ...m, streaming: false, error: true,
            content: m.content || "[连接失败：后端未响应，请确认后端已启动]",
          })),
        },
        ac.signal,
      );
    } finally {
      setStreaming(false);
      patch(target, (m) => ({ ...m, streaming: false }));
      ctrl.current = null;
    }
  }, [streaming, conversationId, conversationRepo]);

  const loadConversation = useCallback(async (id: string) => {
    if (streaming) return;
    const detail = await getConversation(id);
    setConversationId(detail.conversation.id);
    setConversationTitle(detail.conversation.title);
    setConversationRepo(detail.conversation.target_repo);
    setMessages(detail.messages.map((m) => ({
      id: `h${m.id}`,
      role: m.role,
      content: m.content,
      citations: m.meta?.citations ?? [],
      agentSteps: m.meta?.agent_steps,
      retrieval: undefined,
      createdAt: new Date(m.created_at).getTime(),
      messageId: m.id,
    })));
  }, [streaming]);

  const newConversation = useCallback(() => {
    if (streaming) return;
    setConversationId(null);
    setConversationTitle(null);
    setConversationRepo(repo);
    setMessages([]);
  }, [streaming, repo]);

  const setFeedback = useCallback((messageId: number, fb: "HELPFUL" | "NOT_HELPFUL") => {
    setMessages((arr) => arr.map((m) => (m.messageId === messageId ? { ...m, feedback: fb } : m)));
  }, []);

  const stop = useCallback(() => ctrl.current?.abort(), []);
  const clear = useCallback(() => setMessages([]), []);

  return { messages, streaming, send, stop, clear, conversationId, conversationTitle,
           conversationRepo, setConversationRepo, loadConversation, newConversation, setFeedback };
}
