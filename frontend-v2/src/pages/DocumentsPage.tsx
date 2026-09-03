/**
 * 文档管理页（Phase 1.5d-frontend）：上传（md/pdf/docx/txt/...）→ MinIO+解析入库；
 * 文档列表（格式/解析状态/页数/切片/大小）；表格预览抽屉（渲染结构化 table_html）；删除。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Col,
  Drawer,
  Popconfirm,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from "antd";
import { DeleteOutlined, ReloadOutlined, TableOutlined, UploadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  deleteDocument,
  getTableData,
  listDocumentTables,
  listDocuments,
  uploadDocument,
  type DocumentItem,
  type TableListItem,
} from "../api/documents";

const { Text, Paragraph } = Typography;

const ALLOW_EXTS = [".md", ".markdown", ".html", ".pdf", ".docx", ".doc", ".txt"];
const FORMAT_COLOR: Record<string, string> = {
  pdf: "red", docx: "blue", doc: "blue", markdown: "cyan", html: "cyan", txt: "default",
};
const STATUS_COLOR: Record<string, string> = {
  COMPLETED: "success", PARTIAL: "warning", FAILED: "error", PENDING: "default",
};

const fmtSize = (b: number | null | undefined) => {
  if (!b) return "—";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
};
const fmtTime = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [tablesDrawerFile, setTablesDrawerFile] = useState<DocumentItem | null>(null);
  const [tableList, setTableList] = useState<TableListItem[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [previewHtml, setPreviewHtml] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listDocuments({ page_size: 100 });
      setDocs(res.items);
    } catch (e) {
      message.error((e as Error).message || "加载文档列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  const beforeUpload = (file: File) => {
    const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
    if (!ALLOW_EXTS.includes(ext)) {
      message.error(`不支持的格式 ${ext}；支持 ${ALLOW_EXTS.join("/")}`);
      return Upload.LIST_IGNORE;
    }
    return true;
  };

  // antd Upload customRequest 选项（file 字段为用户选择的文件）
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleUpload = async (opt: any) => {
    const file = opt.file as File;
    setUploading(true);
    try {
      const res = await uploadDocument(file);
      message.success(res.message);
      refresh();
    } catch (e) {
      message.error((e as Error).message || "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const onDelete = async (id: number) => {
    try {
      await deleteDocument(id);
      message.success("已删除");
      refresh();
    } catch (e) {
      message.error((e as Error).message || "删除失败");
    }
  };

  const openTables = async (doc: DocumentItem) => {
    setTablesDrawerFile(doc);
    setPreviewHtml(null);
    setTablesLoading(true);
    try {
      const res = await listDocumentTables(doc.file_id);
      setTableList(res.items);
    } catch (e) {
      message.error((e as Error).message || "加载表格失败");
      setTableList([]);
    } finally {
      setTablesLoading(false);
    }
  };

  const previewTable = async (chunkId: string) => {
    try {
      const td = await getTableData(chunkId);
      setPreviewHtml(td.table_html);
    } catch (e) {
      message.error((e as Error).message || "加载表格数据失败");
    }
  };

  const byFormat = docs.reduce<Record<string, number>>((m, d) => {
    const f = d.file_format || "?";
    m[f] = (m[f] || 0) + 1;
    return m;
  }, {});

  const columns: ColumnsType<DocumentItem> = [
    {
      title: "文档",
      dataIndex: "title",
      render: (t: string | null, r) => (
        <Tooltip title={r.storage_path || r.file_path}>
          <Text strong>{t || r.file_path}</Text>
        </Tooltip>
      ),
    },
    {
      title: "格式", dataIndex: "file_format", width: 90,
      render: (f: string | null) => <Tag color={FORMAT_COLOR[f || ""] || "default"}>{f || "—"}</Tag>,
    },
    {
      title: "解析状态", dataIndex: "parse_status", width: 110,
      render: (s: string | null) => <Tag color={STATUS_COLOR[s || ""] || "default"}>{s || "—"}</Tag>,
    },
    { title: "页", dataIndex: "total_pages", width: 60, render: (v) => v ?? "—" },
    { title: "切片", dataIndex: "total_chunks", width: 60 },
    { title: "大小", dataIndex: "file_size_bytes", width: 90, render: fmtSize },
    { title: "上传时间", dataIndex: "created_at", width: 170, render: fmtTime },
    {
      title: "操作", width: 150, fixed: "right",
      render: (_, r) => (
        <Space size={0}>
          <Button type="link" size="small" icon={<TableOutlined />} onClick={() => openTables(r)}>
            表格
          </Button>
          <Popconfirm title="删除该文档？" onConfirm={() => onDelete(r.file_id)} okText="删除" cancelText="取消">
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 20, height: "100%", overflow: "auto" }}>
      <Card size="small" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
        <Row gutter={24} align="middle">
          <Col><Statistic title="已上传文档" value={docs.length} /></Col>
          <Col>
            <Space size={4} wrap>
              {Object.entries(byFormat).map(([f, n]) => (
                <Tag key={f} color={FORMAT_COLOR[f] || "default"}>{f}: {n}</Tag>
              ))}
            </Space>
          </Col>
          <Col flex="auto" />
          <Col>
            <Space>
              <Upload accept={ALLOW_EXTS.join(",")} beforeUpload={beforeUpload}
                customRequest={handleUpload} showUploadList={false}>
                <Button type="primary" icon={<UploadOutlined />} loading={uploading}>上传文档</Button>
              </Upload>
              <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>刷新</Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Card size="small">
        <Table<DocumentItem>
          rowKey="file_id"
          size="small"
          loading={loading}
          dataSource={docs}
          columns={columns}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 920 }}
          locale={{ emptyText: "暂无上传文档，点击右上角「上传文档」" }}
        />
      </Card>

      <Drawer
        title={tablesDrawerFile ? `表格预览 · ${tablesDrawerFile.title || tablesDrawerFile.file_path}` : ""}
        open={!!tablesDrawerFile}
        onClose={() => setTablesDrawerFile(null)}
        width={720}
        destroyOnClose
      >
        {previewHtml && (
          <Card size="small" title="渲染预览" style={{ marginBottom: 12 }}>
            <div className="coderag-html-table" style={{ overflow: "auto" }}
              dangerouslySetInnerHTML={{ __html: previewHtml }} />
          </Card>
        )}
        <Typography.Title level={5}>表格列表 ({tableList.length})</Typography.Title>
        <Table<TableListItem>
          rowKey="chunk_id"
          size="small"
          loading={tablesLoading}
          dataSource={tableList}
          pagination={false}
          columns={[
            { title: "行×列", width: 80, render: (_, r) => `${r.table_total_rows ?? "-"}×${r.table_total_cols ?? "-"}` },
            { title: "说明", dataIndex: "table_description" },
            {
              title: "类型", width: 90,
              render: (_, r) => r.is_table_fragment ? <Tag>分片</Tag> : <Tag color="blue">完整</Tag>,
            },
            {
              title: "操作", width: 80,
              render: (_, r) => (
                <Button type="link" size="small" onClick={() => previewTable(r.chunk_id)}>预览</Button>
              ),
            },
          ]}
        />
        {tableList.length === 0 && !tablesLoading && (
          <Paragraph type="secondary" style={{ marginTop: 12 }}>该文档无结构化表格。</Paragraph>
        )}
      </Drawer>
    </div>
  );
}
