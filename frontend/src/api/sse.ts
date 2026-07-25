import { fetchEventSource } from "@microsoft/fetch-event-source";
import { API_BASE } from "./client";

export interface ChatStreamHandlers {
  onConversation?: (info: unknown) => void;
  onRetrieval?: (info: unknown) => void;
  onToken?: (text: string) => void;
  onCitation?: (citation: unknown) => void;
  onNode?: (info: unknown) => void;
  onDone?: (meta: unknown) => void;
  onError?: (err: unknown) => void;
}

/**
 * SSE 流式问答（Phase 1 后端实现后启用）。
 * 事件类型：token / citation / node_done / done（见技术栈架构设计 §9）。
 */
export async function streamChat(
  payload: Record<string, unknown>,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  await fetchEventSource(`${API_BASE}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
    signal,
    openWhenHidden: true,
    onmessage(ev) {
      switch (ev.event) {
        case "conversation":
          handlers.onConversation?.(safeParse(ev.data));
          break;
        case "retrieval":
          handlers.onRetrieval?.(safeParse(ev.data));
          break;
        case "token":
          handlers.onToken?.(safeParse(ev.data).content ?? "");
          break;
        case "citation":
          handlers.onCitation?.(safeParse(ev.data));
          break;
        case "node_done":
          handlers.onNode?.(safeParse(ev.data));
          break;
        case "done":
          handlers.onDone?.(safeParse(ev.data));
          break;
      }
    },
    onerror(err) {
      handlers.onError?.(err);
      throw err; // 停止重连
    },
  });
}

function safeParse(s: string | undefined): any {
  try {
    return s ? JSON.parse(s) : {};
  } catch {
    return { content: s };
  }
}
