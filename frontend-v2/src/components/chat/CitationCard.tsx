import { Tag, Typography, theme } from "antd";
import { CodeOutlined, FileTextOutlined } from "@ant-design/icons";
import type { Citation } from "../../hooks/types";
import { useAppStore } from "../../stores/app";

/** v2 引用卡片：code（file:line 行号锚点）/ doc（doc#section）；点击聚焦右侧只读预览。 */
export default function CitationCard({ c, index, repo }: { c: Citation; index: number; repo?: string }) {
  const { token } = theme.useToken();
  const setFocused = useAppStore((s) => s.setFocused);
  const selected = false; // 高亮态由 ContextPanel 聚焦对比（kind+file_path+start_line）
  const isCode = c.kind === "code";
  const range = isCode && c.start_line != null
    ? c.start_line === c.end_line ? `:${c.start_line}` : `:${c.start_line}-${c.end_line}`
    : "";

  return (
    <div
      onClick={() => setFocused({ ...c, repo: repo ?? "" })}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "4px 10px", borderRadius: 6, marginRight: 8, marginBottom: 4,
        background: selected ? "rgba(255,106,0,0.15)" : token.colorFillQuaternary,
        border: `1px solid ${token.colorBorderSecondary}`,
        fontSize: 12, maxWidth: "100%", cursor: "pointer",
      }}
    >
      <span style={{ color: token.colorTextTertiary }}>{index + 1}.</span>
      <Tag color={isCode ? "blue" : "gold"} icon={isCode ? <CodeOutlined /> : <FileTextOutlined />} style={{ margin: 0 }}>
        {isCode ? "代码" : "文档"}
      </Tag>
      <Typography.Text ellipsis style={{ maxWidth: 320 }} className="code-font">
        {c.label}
      </Typography.Text>
      {range && (
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>{range}</Typography.Text>
      )}
    </div>
  );
}
