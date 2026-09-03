import { useEffect, useState } from "react";
import { Empty, Spin, Tag, Typography, theme } from "antd";
import { CodeOutlined, FileTextOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAppStore } from "../stores/app";
import { readCode, readDocSection } from "../api/preview";

const { Text } = Typography;

/** 右侧只读上下文面板：聚焦引用 → code 行号窗口 / doc 章节正文（spec §6「行号锚点代码引用（只读展示）」）。 */
export default function ContextPanel() {
  const { token } = theme.useToken();
  const focused = useAppStore((s) => s.focused);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [code, setCode] = useState<{ lines: string[]; start: number; hlFrom: number; hlTo: number } | null>(null);
  const [doc, setDoc] = useState<{ title: string; content: string } | null>(null);

  useEffect(() => {
    setCode(null); setDoc(null); setFailed(false);
    if (!focused) return;
    let cancelled = false;
    setLoading(true);
    const run = async () => {
      try {
        if (focused.kind === "code" && focused.file_path) {
          const from = Math.max(1, (focused.start_line ?? 1) - 5);
          const to = (focused.end_line ?? (focused.start_line ?? 1) + 40) + 5;
          const r = await readCode({ repo: focused.repo, path: focused.file_path, start_line: from, end_line: to });
          if (!cancelled) setCode({
            lines: r.content.split("\n"),
            start: r.start_line,
            hlFrom: focused.start_line ?? r.start_line,
            hlTo: focused.end_line ?? focused.start_line ?? r.start_line,
          });
        } else if (focused.doc_id && focused.section) {
          const r = await readDocSection({ repo: focused.repo, doc_name: focused.doc_id, anchor: focused.section });
          if (!cancelled) setDoc({ title: r.title || focused.section, content: r.content });
        } else {
          setFailed(true);
        }
      } catch {
        if (!cancelled) setFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();
    return () => { cancelled = true; };
  }, [focused]);

  if (!focused) {
    return (
      <div style={{ padding: 16 }}>
        <Text strong>📌 当前上下文</Text>
        <div style={{ marginTop: 12, color: token.colorTextTertiary, fontSize: 13 }}>
          点击聊天中的引用卡片，此处只读展示对应代码窗口 / 文档章节。
        </div>
      </div>
    );
  }
  const isCode = focused.kind === "code";
  return (
    <div style={{ padding: 16, height: "100%", overflow: "auto" }}>
      <Text strong>📌 当前上下文</Text>
      <div style={{ marginTop: 8, marginBottom: 12, padding: "6px 8px", background: "rgba(255,106,0,0.1)", borderRadius: 6 }}>
        <Tag color={isCode ? "blue" : "gold"} icon={isCode ? <CodeOutlined /> : <FileTextOutlined />} style={{ margin: 0 }}>
          {isCode ? "代码" : "文档"}
        </Tag>
        <div className="code-font" style={{ fontSize: 12, marginTop: 4, wordBreak: "break-all" }}>{focused.label}</div>
      </div>
      {loading ? <div style={{ textAlign: "center", padding: 24 }}><Spin /></div>
        : failed ? <Empty description="内容加载失败" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        : code ? (
          <pre className="code-font" style={{ fontSize: 12, lineHeight: "18px", margin: 0, overflow: "auto" }}>
            {code.lines.map((ln, i) => {
              const no = code.start + i;
              const hl = no >= code.hlFrom && no <= code.hlTo;
              return (
                <div key={no} style={{
                  background: hl ? token.colorPrimaryBg : undefined,
                  padding: hl ? "0 4px" : undefined, borderRadius: hl ? 3 : undefined,
                  whiteSpace: "pre",
                }}>
                  <span style={{ display: "inline-block", width: 40, color: token.colorTextQuaternary, textAlign: "right", paddingRight: 8 }}>{no}</span>
                  {ln}
                </div>
              );
            })}
          </pre>
        ) : doc ? (
          <div className="chat-md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.content}</ReactMarkdown></div>
        ) : null}
    </div>
  );
}
