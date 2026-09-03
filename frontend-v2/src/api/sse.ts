import { fetchEventSource } from "@microsoft/fetch-event-source";
import { API_BASE, getToken } from "./client";

export interface ChatStreamHandlers {
  onConversation?: (info: unknown) => void;
  onRetrieval?: (info: unknown) => void;
  onToken?: (text: string) => void;
  onCitation?: (citation: unknown) => void;
  onAgentStep?: (step: unknown) => void;
  onNode?: (info: unknown) => void;
  onInterrupt?: (info: unknown) => void; // HITL（M10）：图暂停，前端弹审批框
  onDone?: (meta: unknown) => void;
  onError?: (err: unknown) => void;
}

/**
 * SSE 流式问答（Phase 1 后端实现后启用）。
 * 事件类型：token / citation / agent_step / node_done / done
 * （agent_step 仅 RAG_ENGINE=langgraph 的场景 Agent 路径推送，见技术栈架构设计 §9）。
 */
/** 把一条 SSE 事件分发给对应回调（streamChat / streamResume 共用）。 */
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
    case "node_done":
      handlers.onNode?.(safeParse(ev.data));
      break;
    case "interrupt": // HITL（M10）：图暂停，前端弹审批框
      handlers.onInterrupt?.(safeParse(ev.data));
      break;
    case "done":
      handlers.onDone?.(safeParse(ev.data));
      break;
  }
}

/**
 * SSE 流式问答（Phase 1 后端实现后启用）。
 * 事件类型：token / citation / agent_step / node_done / interrupt / done
 * （agent_step 仅 RAG_ENGINE=langgraph 的场景 Agent 路径推送；interrupt 仅 HITL 分支，见技术栈架构设计 §9）。
 */
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

/**
 * HITL 续跑（M10）：对一条 interrupted 态消息给出人工决策，POST /v1/chat/resume，
 * 续跑主图并流式产出 token → done（事件分发同 streamChat）。
 */
export async function streamResume(
  payload: Record<string, unknown>,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  await fetchEventSource(`${API_BASE}/v1/chat/resume`, {
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
