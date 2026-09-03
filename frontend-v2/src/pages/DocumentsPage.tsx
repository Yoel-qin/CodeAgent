/** v2 文档管理页：repo 过滤 + 文档列表 + 章节抽屉（text/table 只读浏览）。 */
import { useCallback, useEffect, useState } from "react";
import { Button, Card, Col, Drawer, Row, Select, Space, Spin, Table, Tag, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { listRepos } from "../api/repos";
import { getDocumentSections, listDocuments, type DocSectionItem, type DocumentItem } from "../api/documents";

const { Text, Paragraph } = Typography;
const TYPE_COLOR: Record<string, string> = { markdown: "cyan", pdf: "red", docx: "blue", txt: "default" };
const STATUS_COLOR: Record<string, string> = { COMPLETED: "success", PARTIAL: "warning", FAILED: "error" };
const fmtTime = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [repos, setRepos] = useState<string[]>([]);
  const [repo, setRepo] = useState<string | undefined>(undefined);
  const [drawerDoc, setDrawerDoc] = useState<DocumentItem | null>(null);
  const [sections, setSections] = useState<DocSectionItem[]>([]);
  const [sectionsLoading, setSectionsLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listDocuments({ repo, limit: 200 });
      setDocs(res.items);
    } catch (e) {
      message.error((e as Error).message || "加载文档列表失败");
    } finally {
      setLoading(false);
    }
  }, [repo]);

  useEffect(() => { listRepos().then((r) => setRepos(r.items)).catch(() => setRepos([])); }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const openSections = async (d: DocumentItem) => {
    setDrawerDoc(d);
    setSections([]);
    setSectionsLoading(true);
    try {
      const res = await getDocumentSections(d.id);
      setSections(res.sections);
    } catch (e) {
      message.error((e as Error).message || "加载章节失败");
    } finally {
      setSectionsLoading(false);
    }
  };

  const columns: ColumnsType<DocumentItem> = [
    { title: "文档", dataIndex: "doc_name", render: (n, r) => (
      <Button type="link" size="small" style={{ padding: 0 }} onClick={() => openSections(r)}>
        <Text ellipsis style={{ maxWidth: 320 }}>{n}</Text>
      </Button>
    ) },
    { title: "仓库", dataIndex: "repo", width: 110 },
    { title: "模块", dataIndex: "module", width: 130, render: (m) => m ?? "—" },
    { title: "格式", dataIndex: "doc_type", width: 90,
      render: (t) => <Tag color={TYPE_COLOR[t] || "default"}>{t}</Tag> },
    { title: "状态", dataIndex: "status", width: 100,
      render: (s) => <Tag color={STATUS_COLOR[s] || "default"}>{s}</Tag> },
    { title: "章节", dataIndex: "section_count", width: 70 },
    { title: "入库时间", dataIndex: "created_at", width: 170, render: fmtTime },
  ];

  return (
    <div style={{ padding: 20, height: "100%", overflow: "auto" }}>
      <Card size="small" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col>仓库</Col>
          <Col>
            <Select allowClear placeholder="全部仓库" style={{ width: 180 }} value={repo}
              onChange={(v) => setRepo(v)} options={repos.map((r) => ({ value: r, label: r }))} />
          </Col>
          <Col flex="auto" />
          <Col><Text type="secondary">共 {docs.length} 篇（入库经 ingest CLI / git webhook 管道）</Text></Col>
          <Col><Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>刷新</Button></Col>
        </Row>
      </Card>
      <Card size="small">
        <Table<DocumentItem> rowKey="id" size="small" loading={loading} dataSource={docs} columns={columns}
          pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 860 }}
          locale={{ emptyText: "暂无文档——先跑 scripts/ingest_docs.py 或推送 webhook" }} />
      </Card>
      <Drawer title={drawerDoc ? `章节 · ${drawerDoc.doc_name}` : ""} open={!!drawerDoc}
        onClose={() => setDrawerDoc(null)} width={620} destroyOnClose>
        <Spin spinning={sectionsLoading}>
          {sections.length === 0 && !sectionsLoading ? (
            <Paragraph type="secondary">无章节。</Paragraph>
          ) : sections.map((s) => (
            <Card key={s.id} size="small" style={{ marginBottom: 8 }}
              title={<Space>{s.level != null && <Text type="secondary">H{s.level}</Text>}{s.title || s.anchor}
                {s.kind === "table" && <Tag color="geekblue">表格</Tag>}</Space>}
              extra={<Text type="secondary" style={{ fontSize: 12 }}>{s.token_count} tok{s.page != null ? ` · p${s.page}` : ""}</Text>}>
              <Paragraph style={{ fontSize: 12, marginBottom: 0, whiteSpace: "pre-wrap", maxHeight: 180, overflow: "auto" }}>
                {s.content}
              </Paragraph>
            </Card>
          ))}
        </Spin>
      </Drawer>
    </div>
  );
}
