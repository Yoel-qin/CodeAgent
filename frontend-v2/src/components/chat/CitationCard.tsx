import { useState } from "react";
import { Button, Image, Modal, Spin, Tag, Typography, theme } from "antd";
import {
  CodeOutlined,
  FileTextOutlined,
  PictureOutlined,
  TableOutlined,
} from "@ant-design/icons";
import type { Citation } from "../../hooks/useChat";
import { API_BASE } from "../../api/client";
import { getTableData } from "../../api/documents";
import { useAppStore } from "../../stores/app";

const _FALLBACK =
  "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI1NiIgaGVpZ2h0PSI1NSI+PHJlY3Qgd2lkdGg9IjU1IiBoZWlnaHQ9IjU1IiBmaWxsPSIjNDQ0Ii8+PHRleHQgeD0iOCIgeT0iMzIiIGZvbnQtc2l6ZT0iMTAiIGZpbGw9IiNhYWEiPmltZzwvdGV4dD48L3N2Zz4=";

/** 引用卡片：代码（类.方法）/ 文档（章节）/ 图片（缩略图+灯箱）/ 表格（预览）。 */
export default function CitationCard({ c, index }: { c: Citation; index: number }) {
  const { token } = theme.useToken();
  const ct = c.content_type || "text";
  const isCode = c.type === "code";
  const isImage = ct === "image";
  const isTable = ct === "table" || ct === "table_fragment";

  const [tableOpen, setTableOpen] = useState(false);
  const [tableHtml, setTableHtml] = useState<string | null>(null);
  const [tableLoading, setTableLoading] = useState(false);

  const setFocused = useAppStore((s) => s.setFocused);
  const selected = useAppStore((s) => s.focused?.chunk_id === c.chunk_id);
  const onFocus = () =>
    setFocused({ chunk_id: c.chunk_id, type: c.type === "code" ? "code" : "doc", label: c.label });

  const pillStyle: React.CSSProperties = {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "4px 10px", borderRadius: 6, marginRight: 8, marginBottom: 4,
    background: selected ? "rgba(255,106,0,0.15)" : token.colorFillQuaternary,
    border: `1px solid ${selected ? "var(--brand)" : token.colorBorderSecondary}`,
    fontSize: 12, maxWidth: "100%", cursor: "pointer",
  };

  const openTable = async () => {
    setTableOpen(true);
    if (tableHtml == null) {
      setTableLoading(true);
      try {
        const td = await getTableData(c.chunk_id);
        setTableHtml(td.table_html || "");
      } catch {
        setTableHtml("");
      } finally {
        setTableLoading(false);
      }
    }
  };

  // 图片：缩略图 + 点击灯箱看原图
  if (isImage) {
    return (
      <div style={{ ...pillStyle, gap: 8 }} onClick={onFocus}>
        <span style={{ color: token.colorTextTertiary }}>{index + 1}.</span>
        <Image
          width={56}
          height={56}
          src={`${API_BASE}/v1/resources/${c.chunk_id}/thumbnail`}
          preview={{ src: `${API_BASE}/v1/resources/${c.chunk_id}/image` }}
          fallback={_FALLBACK}
          style={{ borderRadius: 4, objectFit: "cover" }}
        />
        <div style={{ maxWidth: 220 }}>
          <Tag color="purple" icon={<PictureOutlined />} style={{ margin: 0 }}>图片</Tag>
          <Typography.Text ellipsis type="secondary" style={{ display: "block", maxWidth: 220 }}>
            {c.label}
          </Typography.Text>
        </div>
      </div>
    );
  }

  return (
    <div style={pillStyle} onClick={onFocus}>
      <span style={{ color: token.colorTextTertiary }}>{index + 1}.</span>
      <Tag
        color={isCode ? "blue" : isTable ? "geekblue" : "gold"}
        icon={isCode ? <CodeOutlined /> : isTable ? <TableOutlined /> : <FileTextOutlined />}
        style={{ margin: 0 }}
      >
        {isCode ? "代码" : isTable ? "表格" : "文档"}
      </Tag>
      <Typography.Text ellipsis style={{ maxWidth: 300 }} className="code-font">
        {c.label}
      </Typography.Text>
      {isTable && (
        <Button type="link" size="small" icon={<TableOutlined />} onClick={openTable}>查看</Button>
      )}
      {c.score != null && (
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {c.score.toFixed(2)}
        </Typography.Text>
      )}
      <Modal title="表格预览" open={tableOpen} onCancel={() => setTableOpen(false)}
        footer={null} width={720} destroyOnClose>
        {tableLoading ? (
          <Spin />
        ) : tableHtml ? (
          <div className="coderag-html-table" style={{ overflow: "auto" }}
            dangerouslySetInnerHTML={{ __html: tableHtml }} />
        ) : (
          <Typography.Text type="secondary">无表格数据</Typography.Text>
        )}
      </Modal>
    </div>
  );
}
