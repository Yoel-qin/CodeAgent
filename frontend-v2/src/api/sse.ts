import { EventStreamContentType, fetchEventSource } from "@microsoft/fetch-event-source";
import { API_BASE, clearToken, getToken } from "./client";

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
    onopen: async (res) => {
      // RBAC on + JWT 过期/无效：SSE 不走 axios 拦截器 → 此处同款处置（清 token 回登录）
      // 后 throw 终止流；页面已整页跳转，onerror 的「连接失败」气泡不会停留。
      if (res.status === 401) {
        clearToken();
        localStorage.removeItem("coderag_username");
        if (!window.location.pathname.startsWith("/login")) {
          window.location.href = "/login";
        }
      }
      // 提供 onopen 会整体覆盖库默认校验（defaultOnOpen），此处逐条复制：
      if (!res.ok) {
        throw new Error(`Server responded with ${res.status}: ${res.statusText}`);
      }
      if (!res.headers.get("content-type")?.startsWith(EventStreamContentType)) {
        throw new Error(`Expected content-type to be ${EventStreamContentType}, Actual: ${res.headers.get("content-type")}`);
      }
    },
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
