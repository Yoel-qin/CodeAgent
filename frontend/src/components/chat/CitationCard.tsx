import { Tag, Typography, theme } from "antd";
import { CodeOutlined, FileTextOutlined } from "@ant-design/icons";
import type { Citation } from "../../hooks/useChat";

/** 引用卡片：代码（类.方法）/ 文档（章节面包屑）。 */
export default function CitationCard({ c, index }: { c: Citation; index: number }) {
  const { token } = theme.useToken();
  const isCode = c.type === "code";
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 10px",
        borderRadius: 6,
        marginRight: 8,
        marginBottom: 4,
        background: token.colorFillQuaternary,
        border: `1px solid ${token.colorBorderSecondary}`,
        fontSize: 12,
        maxWidth: "100%",
      }}
    >
      <span style={{ color: token.colorTextTertiary }}>{index + 1}.</span>
      <Tag
        color={isCode ? "blue" : "gold"}
        icon={isCode ? <CodeOutlined /> : <FileTextOutlined />}
        style={{ margin: 0 }}
      >
        {isCode ? "代码" : "文档"}
      </Tag>
      <Typography.Text ellipsis style={{ maxWidth: 360 }} className="code-font">
        {c.label}
      </Typography.Text>
      {c.score != null && (
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {c.score.toFixed(2)}
        </Typography.Text>
      )}
    </div>
  );
}
