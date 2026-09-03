import { fetchEventSource } from "@microsoft/fetch-event-source";
import { API_BASE, getToken } from "./client";

export interface ChatStreamHandlers {
  onConversation?: (info: unknown) => void;
  onRetrieval?: (info: unknown) => void;
  onToken?: (text: string) => void;
  onCitation?: (citation: unknown) => void;
  onAgentStep?: (step: unknown) => void;
  onDone?: (meta: unknown) => void;
  onError?: (err: unknown) => void;
}

/**
 * SSE 流式问答（v2 契约：conversation / retrieval / citation / token / agent_step / done）。
 * 事件序不保证（ReAct 收尾 drain：token → agent_step → citation）——回调只按类型分流累积。
 */
function handleEvent(
  ev: { event: string; data: string | undefined },
  handlers: ChatStreamHandlers,
) {
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
    case "agent_step":
      handlers.onAgentStep?.(safeParse(ev.data));
      break;
    case "done":
      handlers.onDone?.(safeParse(ev.data));
      break;
  }
}

export async function streamChat(
  payload: Record<string, unknown>,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  await fetchEventSource(`${API_BASE}/v1/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    },
    body: JSON.stringify(payload),
    signal,
    openWhenHidden: true,
    onmessage: (ev) => handleEvent(ev, handlers),
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
