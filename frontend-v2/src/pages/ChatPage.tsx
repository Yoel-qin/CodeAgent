import { useEffect, useMemo, useState } from "react";
import {
  Input,
  Button,
  Select,
  Space,
  Typography,
  Empty,
  Tooltip,
  theme,
} from "antd";
import {
  SendOutlined,
  StopOutlined,
  DeleteOutlined,
  RobotOutlined,
  UserOutlined,
  LikeOutlined,
  DislikeOutlined,
  FundProjectionScreenOutlined,
} from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useChat } from "../hooks/useChat";
import type { AgentStep, ChatMessage, RetrievalInfo } from "../hooks/types";
import { listRepos } from "../api/repos";
import { postFeedback } from "../api/conversations";
import CitationCard from "../components/chat/CitationCard";
import ConversationList from "../components/chat/ConversationList";
import RetrievalDrawer from "../components/chat/RetrievalDrawer";

const { TextArea } = Input;
const { Text } = Typography;

type Rating = "HELPFUL" | "NOT_HELPFUL";

// 前端不选 Agent：route 由后端意图路由产出（retrieval 事件 mode / 历史 meta.route），此映射仅作标签回显。
const ROUTE_LABELS: Record<string, string> = {
  codenav: "代码导航",
  docqa: "文档问答",
  retrieve: "检索",
  clarify: "澄清",
};

export default function ChatPage() {
  const { token } = theme.useToken();
  const {
    messages, streaming, send, stop, clear,
    conversationId, conversationTitle, conversationRepo, setConversationRepo,
    loadConversation, newConversation, setFeedback,
  } = useChat("");
  const [value, setValue] = useState("");
  const [drawerMsg, setDrawerMsg] = useState<ChatMessage | null>(null);
  const [repos, setRepos] = useState<string[]>([]);

  useEffect(() => {
    listRepos().then((r) => setRepos(r.items)).catch(() => setRepos([]));
  }, []);

  const submit = (q?: string) => {
    const text = (q ?? value).trim();
    if (!text || streaming) return;
    setValue("");
    void send(text);
  };

  const lastAssistant = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && m.messageId && !m.streaming) return m;
    }
    return null;
  }, [messages]);

  // 反馈简单二态（v2 无六分类/纠错）：NOT_HELPFUL 也直接发送，无弹窗
  const handleFeedback = async (m: ChatMessage, rating: Rating) => {
    if (!m.messageId) return;
    try {
      await postFeedback(m.messageId, rating);
      setFeedback(m.messageId, rating);
    } catch {
      /* 反馈失败静默 */
    }
  };

  return (
    <div style={{ display: "flex", height: "100%" }}>
      {/* 左：会话列表 */}
      <div style={{ width: 220, flexShrink: 0, background: token.colorBgContainer }}>
        <ConversationList
          activeId={conversationId}
          onSelect={loadConversation}
          onNew={newConversation}
        />
      </div>

      {/* 中：对话区 */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* 对话流 */}
        <div style={{ flex: 1, overflow: "auto", padding: "20px 24px" }}>
          {messages.length === 0 ? (
            <Empty
              style={{ marginTop: 72 }}
              description={
                <Text type="secondary">
                  输入问题开始对话。例如：<Text code>checkLocalTransaction 是做什么的</Text>
                </Text>
              }
            />
          ) : (
            messages.map((m) => (
              <MessageRow
                key={m.id}
                m={m}
                token={token}
                isLastAssistant={lastAssistant?.id === m.id}
                repo={conversationRepo}
                streaming={streaming}
                onOpenRetrieval={setDrawerMsg}
                onFeedback={handleFeedback}
              />
            ))
          )}
        </div>

        {/* 输入区 */}
        <div
          style={{
            borderTop: `1px solid ${token.colorBorderSecondary}`,
            background: token.colorBgContainer,
            padding: 12,
          }}
        >
          <Select
            value={conversationRepo || undefined}
            onChange={(v) => setConversationRepo(v ?? "")}
            options={repos.map((r) => ({ value: r, label: r }))}
            placeholder="默认仓库"
            style={{ width: 160, marginBottom: 8 }}
          />
          <Space.Compact style={{ width: "100%" }}>
            <TextArea
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="输入问题…（支持代码标识符，如 DefaultMQPushConsumer；回车发送，Shift+回车换行）"
              autoSize={{ minRows: 1, maxRows: 4 }}
              variant="filled"
              style={{ borderRadius: 0 }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            {streaming ? (
              <Button danger icon={<StopOutlined />} onClick={stop}>
                停止
              </Button>
            ) : (
              <Button type="primary" icon={<SendOutlined />} onClick={() => submit()} disabled={!value.trim()}>
                发送
              </Button>
            )}
          </Space.Compact>
          <Space style={{ marginTop: 8 }}>
            <Tooltip title="清空当前会话消息（不清除已保存的历史）">
              <Button size="small" icon={<DeleteOutlined />} onClick={clear} disabled={streaming || !messages.length} />
            </Tooltip>
            <Text type="secondary" style={{ fontSize: 12 }}>
              CodeRAG · 意图路由 · 混合检索增强生成
              {conversationTitle ? ` · ${conversationTitle}` : ""}
            </Text>
          </Space>
        </div>
      </div>

      {/* Agent 轨迹抽屉（props 驱动：数据来自消息 state / 历史 meta） */}
      <RetrievalDrawer
        open={!!drawerMsg}
        onClose={() => setDrawerMsg(null)}
        steps={drawerMsg?.agentSteps ?? null}
        retrieval={drawerMsg?.retrieval ?? null}
      />
    </div>
  );
}

function MessageRow({
  m, token, isLastAssistant, repo, streaming, onOpenRetrieval, onFeedback,
}: {
  m: ChatMessage;
  token: ReturnType<typeof theme.useToken>["token"];
  isLastAssistant: boolean;
  repo: string;
  streaming: boolean;
  onOpenRetrieval: (m: ChatMessage) => void;
  onFeedback: (m: ChatMessage, rating: Rating) => void;
}) {
  const isUser = m.role === "user";
  const bubbleBg = isUser ? token.colorPrimaryBg : token.colorBgContainer;
  return (
    <div style={{ marginBottom: 20, display: "flex", flexDirection: "column", alignItems: isUser ? "flex-end" : "flex-start" }}>
      <div style={{ marginBottom: 4, fontSize: 12, color: token.colorTextTertiary }}>
        {isUser ? (
          <>
            <UserOutlined /> 你
          </>
        ) : (
          <>
            <RobotOutlined /> {m.route ? ROUTE_LABELS[m.route] ?? "助手" : "助手"}
          </>
        )}
        {m.streaming && " · 生成中…"}
      </div>

      <div
        style={{
          maxWidth: "86%",
          padding: "10px 14px",
          borderRadius: 10,
          background: bubbleBg,
          border: `1px solid ${m.error ? token.colorError : token.colorBorderSecondary}`,
          wordBreak: "break-word",
        }}
      >
        {m.content ? (
          <div className="chat-md">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
            {m.streaming && <span className="cursor">▍</span>}
          </div>
        ) : m.streaming ? (
          <Text type="secondary">正在检索与生成…</Text>
        ) : null}
      </div>

      {/* 引用 + 检索信息 + 反馈 */}
      {!isUser && (m.citations.length > 0 || m.retrieval || m.agentSteps?.length) && (
        <div style={{ maxWidth: "86%", marginTop: 6 }}>
          {(m.retrieval || m.agentSteps?.length) && (
            <RetrievalSummary r={m.retrieval} steps={m.agentSteps} token={token} />
          )}
          {m.citations.length > 0 && (
            <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap" }}>
              {m.citations.map((c, i) => (
                <CitationCard key={`${c.kind}_${c.label}_${i}`} c={c} index={i} repo={repo} />
              ))}
            </div>
          )}

          {/* 操作行：Agent 轨迹 + 反馈 */}
          {m.messageId && (
            <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 4 }}>
              <Tooltip title="查看检索摘要与工具调用轨迹">
                <Button
                  size="small"
                  type="text"
                  icon={<FundProjectionScreenOutlined />}
                  onClick={() => onOpenRetrieval(m)}
                >
                  检索详情
                </Button>
              </Tooltip>
              <Tooltip title="有帮助">
                <Button
                  size="small"
                  type="text"
                  icon={<LikeOutlined />}
                  style={{ color: m.feedback === "HELPFUL" ? token.colorSuccess : undefined }}
                  onClick={() => onFeedback(m, "HELPFUL")}
                />
              </Tooltip>
              <Tooltip title="无帮助">
                <Button
                  size="small"
                  type="text"
                  icon={<DislikeOutlined />}
                  style={{ color: m.feedback === "NOT_HELPFUL" ? token.colorError : undefined }}
                  onClick={() => onFeedback(m, "NOT_HELPFUL")}
                />
              </Tooltip>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RetrievalSummary({
  r,
  steps,
  token,
}: {
  r?: RetrievalInfo;
  steps?: AgentStep[];
  token: ReturnType<typeof theme.useToken>["token"];
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 6,
        color: token.colorTextTertiary,
        fontSize: 11,
      }}
    >
      {r && (
        <span>
          {r.mode}
          {r.intent ? ` · ${r.intent}` : ""}
          {r.confidence != null ? ` · ${Math.round(r.confidence * 100)}%` : ""}
        </span>
      )}
      {!!steps?.length && (
        <>
          {r && <span>→</span>}
          <span>🔧 {steps.length} 步</span>
          {steps.map((s, i) => (
            <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              {i > 0 && <span>→</span>}
              <span
                style={{
                  padding: "0 6px",
                  borderRadius: 8,
                  background: token.colorPrimaryBg,
                  lineHeight: "18px",
                }}
              >
                {s.tool}
                {s.n ? ` ·${s.n}` : ""}
              </span>
            </span>
          ))}
        </>
      )}
    </div>
  );
}
