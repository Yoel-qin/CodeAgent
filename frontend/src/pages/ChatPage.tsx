import { useMemo, useState } from "react";
import {
  Input,
  Button,
  Select,
  Space,
  Typography,
  Empty,
  Tooltip,
  Modal,
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
import { useChat, type ChatMessage, type RetrievalInfo, type AgentStep, type Feedback } from "../hooks/useChat";
import { postFeedback } from "../api/conversations";
import CitationCard from "../components/chat/CitationCard";
import ConversationList from "../components/chat/ConversationList";
import RetrievalDrawer from "../components/chat/RetrievalDrawer";
import Suggestions from "../components/chat/Suggestions";

const { TextArea } = Input;
const { Text } = Typography;

const AGENTS = [
  { value: "CODE_UNDERSTAND", label: "代码理解 Agent" },
  { value: "DOC_ANSWER", label: "文档问答 Agent" },
  { value: "CHANGE_IMPACT", label: "变更影响 Agent" },
  { value: "BUG_DIAGNOSIS", label: "缺陷诊断 Agent" },
  { value: "CODE_REVIEW", label: "代码审查 Agent" }, // M11：主动评估代码质量/改进建议
  { value: "TEST_GENERATION", label: "测试生成 Agent" }, // M12：为方法生成 JUnit 单元测试
  { value: "DOC_MAINTAIN", label: "文档维护 Agent" }, // HITL（M10）：人在回路审批
];

export default function ChatPage() {
  const { token } = theme.useToken();
  const {
    messages, streaming, send, resume, stop, clear,
    conversationId, conversationTitle, loadConversation, newConversation, setFeedback,
  } = useChat();
  const [agent, setAgent] = useState("CODE_UNDERSTAND");
  const [value, setValue] = useState("");
  const [drawerMsgId, setDrawerMsgId] = useState<string | null>(null);
  const [hitlComment, setHitlComment] = useState("");

  // HITL（M10）：当前等待人工确认的消息（至多一条）
  const awaiting = messages.find((m) => m.interrupt?.awaiting) ?? null;

  const submit = (q?: string) => {
    const text = (q ?? value).trim();
    if (!text || streaming) return;
    setValue("");
    void send(text, agent);
  };

  const lastAssistant = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && m.messageId && !m.streaming) return m;
    }
    return null;
  }, [messages]);

  const handleFeedback = async (m: ChatMessage, rating: Feedback) => {
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
                conversationId={conversationId}
                streaming={streaming}
                onOpenRetrieval={(id) => setDrawerMsgId(id)}
                onFeedback={handleFeedback}
                onPickSuggestion={(t) => submit(t)}
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
          <Space.Compact style={{ width: "100%" }}>
            <Select value={agent} onChange={setAgent} options={AGENTS} style={{ width: 190 }} />
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
              CodeRAG · RRF + 精排检索增强生成
              {conversationTitle ? ` · ${conversationTitle}` : ""}
            </Text>
          </Space>
        </div>
      </div>

      {/* 检索详情抽屉 */}
      <RetrievalDrawer
        messageId={drawerMsgId}
        open={!!drawerMsgId}
        onClose={() => setDrawerMsgId(null)}
      />

      {/* HITL（M10）审批框：图暂停待人工确认 */}
      <Modal
        title="人工确认 · 文档维护"
        open={!!awaiting}
        onOk={() => {
          void resume(true, hitlComment || undefined);
          setHitlComment("");
        }}
        onCancel={() => {
          void resume(false);
          setHitlComment("");
        }}
        okText="批准并应用"
        cancelText="拒绝"
        confirmLoading={streaming}
        cancelButtonProps={{ disabled: streaming }}
        maskClosable={false}
        keyboard={false}
      >
        <Text type="secondary">系统提议执行以下写动作（标记锚点过时），需人工确认后才会应用：</Text>
        <div
          style={{
            margin: "12px 0",
            padding: 12,
            background: token.colorFillQuaternary,
            borderRadius: 8,
            whiteSpace: "pre-wrap",
          }}
        >
          {awaiting?.interrupt?.proposal}
        </div>
        <TextArea
          value={hitlComment}
          onChange={(e) => setHitlComment(e.target.value)}
          placeholder="备注（可选，将作为 stale_reason 记录）"
          autoSize={{ minRows: 1, maxRows: 3 }}
        />
      </Modal>
    </div>
  );
}

function MessageRow({
  m, token, isLastAssistant, conversationId, streaming, onOpenRetrieval, onFeedback, onPickSuggestion,
}: {
  m: ChatMessage;
  token: ReturnType<typeof theme.useToken>["token"];
  isLastAssistant: boolean;
  conversationId: string | null;
  streaming: boolean;
  onOpenRetrieval: (messageId: string) => void;
  onFeedback: (m: ChatMessage, rating: Feedback) => void;
  onPickSuggestion: (text: string) => void;
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
            <RobotOutlined /> {m.agent ? AGENTS.find((a) => a.value === m.agent)?.label ?? "助手" : "助手"}
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
          border: `1px solid ${token.colorBorderSecondary}`,
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
        ) : m.interrupt?.awaiting ? (
          <Text type="warning">⏳ 已暂停，等待人工确认…</Text>
        ) : null}
      </div>

      {/* 引用 + 检索信息 + 反馈 */}
      {!isUser && (m.citations.length > 0 || m.retrieval) && (
        <div style={{ maxWidth: "86%", marginTop: 6 }}>
          {m.retrieval && <RetrievalSummary r={m.retrieval} steps={m.agentSteps} token={token} />}
          {m.citations.length > 0 && (
            <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap" }}>
              {m.citations.map((c, i) => (
                <CitationCard key={`${c.chunk_id}_${i}`} c={c} index={i} />
              ))}
            </div>
          )}

          {/* 操作行：检索详情 + 反馈 */}
          {m.messageId && (
            <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 4 }}>
              <Tooltip title="查看三阶段检索漏斗与候选">
                <Button
                  size="small"
                  type="text"
                  icon={<FundProjectionScreenOutlined />}
                  onClick={() => onOpenRetrieval(m.messageId!)}
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

          {/* 追问建议（仅最后一条 assistant 消息） */}
          {isLastAssistant && conversationId && m.messageId && (
            <Suggestions
              conversationId={conversationId}
              lastMessageId={m.messageId}
              disabled={streaming}
              onPick={onPickSuggestion}
            />
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
  r: RetrievalInfo;
  steps?: AgentStep[];
  token: ReturnType<typeof theme.useToken>["token"];
}) {
  // 场景 Agent 消息（mode:agent）：recall 漏斗全零，改渲染工具调用轨迹（实时进度，M5 可观测性）
  if (steps?.length) {
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
      </div>
    );
  }

  const recall = r.recall;
  const vec = recall?.vector ?? r.vector ?? 0;
  const lex = recall?.lexical ?? r.lexical ?? 0;
  const grh = recall?.graph ?? r.graph ?? 0;

  const pill = (label: string, n: number, color: string) => (
    <span
      style={{
        padding: "0 6px",
        borderRadius: 8,
        background: color,
        fontSize: 11,
        lineHeight: "18px",
      }}
    >
      {label} {n}
    </span>
  );

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
      {pill("向量", vec, token.colorPrimaryBg)}
      {pill("词法", lex, token.colorFillTertiary)}
      {pill("图遍历", grh, token.colorSuccessBg)}
      <span>→</span>
      {pill("RRF", r.rrf_pool ?? r.merged ?? 0, token.colorInfoBg)}
      {r.rerank_on ? (
        <>
          <span>→</span>
          {pill("粗排", r.coarse ?? 0, token.colorWarningBg)}
          <span>→</span>
          {pill("精排", r.fine ?? 0, token.colorErrorBg)}
        </>
      ) : (
        <span style={{ fontSize: 11 }}>（未启用精排，按 RRF 排序）</span>
      )}
      {r.terms?.length ? <span>· 词：{r.terms.slice(0, 8).join(", ")}</span> : null}
    </div>
  );
}
