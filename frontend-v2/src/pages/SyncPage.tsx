/**
 * 同步管道页（M6 v2）：pipeline_events 账本只读（状态卡 + 事件表 + 状态过滤 + 20s 轮询）
 * + 「模拟 Push」Modal（手动 POST /v1/sync/webhook 入队，仅供联调管道）。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { listRepos } from "../api/repos";
import { listSyncEvents, sendWebhook, type PipelineEventItem } from "../api/sync";

const { Text } = Typography;

const short = (h: string | null | undefined, n = 8) => (h ? h.slice(0, n) : "—");
const fmtTime = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";

const KIND_LABEL: Record<string, string> = { file: "文件", graph_rebuild: "重建图" };
const STATUS_COLOR: Record<string, string> = { DONE: "success", DEAD: "error", PENDING: "default" };
const STATUS_OPTS = [
  { value: "PENDING", label: "PENDING" },
  { value: "DONE", label: "DONE" },
  { value: "DEAD", label: "DEAD" },
];

/** 状态卡计数（reduce 自最近 200 条，超长仓库仅作趋势参考）。 */
interface Stats {
  total: number;
  PENDING: number;
  DONE: number;
  DEAD: number;
}

/** 每行 `path status`（如 `a/B.java M`）→ [{path, status}]；格式错返回 null 并提示。 */
const parseFiles = (raw: string): { path: string; status: string }[] | null => {
  const files: { path: string; status: string }[] = [];
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const parts = trimmed.split(/\s+/);
    if (parts.length !== 2) {
      message.error(`格式错误：「${trimmed}」（每行应为「路径 状态」，如 a/B.java M）`);
      return null;
    }
    files.push({ path: parts[0], status: parts[1] });
  }
  if (files.length === 0) {
    message.error("请至少填写一行「路径 状态」");
    return null;
  }
  return files;
};

export default function SyncPage() {
  const [events, setEvents] = useState<PipelineEventItem[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [repos, setRepos] = useState<string[]>([]);
  const [pushOpen, setPushOpen] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [form] = Form.useForm<{ repo: string; commit_hash: string; files: string }>();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const all = await listSyncEvents({ limit: 200 });
      setStats({
        total: all.total,
        PENDING: all.items.filter((e) => e.status === "PENDING").length,
        DONE: all.items.filter((e) => e.status === "DONE").length,
        DEAD: all.items.filter((e) => e.status === "DEAD").length,
      });
      setEvents(statusFilter ? (await listSyncEvents({ status: statusFilter, limit: 200 })).items : all.items);
    } catch (e) {
      message.error((e as Error).message || "加载管道事件失败");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 20_000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    listRepos().then((r) => setRepos(r.items)).catch(() => setRepos([]));
  }, []);

  const onPush = async () => {
    let values: { repo: string; commit_hash: string; files: string };
    try {
      values = await form.validateFields();
    } catch {
      return; // 校验失败：antd 已在表单项上标红，无需提示
    }
    const files = parseFiles(values.files);
    if (!files) return;
    setPushing(true);
    try {
      const res = await sendWebhook({
        repo: values.repo,
        commit_hash: values.commit_hash.trim(),
        files,
      });
      message.success(`已入队推送事件（event_id=${res.event_id}），等待 worker 消费`);
      setPushOpen(false);
      form.resetFields();
      refresh();
    } catch (e) {
      message.error((e as Error).message || "推送失败");
    } finally {
      setPushing(false);
    }
  };

  const columns: ColumnsType<PipelineEventItem> = [
    { title: "#", dataIndex: "id", width: 64 },
    { title: "仓库", dataIndex: "repo", width: 110 },
    {
      title: "类型",
      dataIndex: "event_kind",
      width: 90,
      render: (k: string) => <Tag color={k === "graph_rebuild" ? "purple" : "blue"}>{KIND_LABEL[k] || k}</Tag>,
    },
    {
      title: "提交",
      dataIndex: "commit_hash",
      width: 100,
      render: (h: string) => (h ? <Tooltip title={h}>{short(h)}</Tooltip> : "—"),
    },
    {
      title: "路径",
      dataIndex: "path",
      render: (p: string) => (
        <Tooltip title={p}>
          <Text code style={{ fontSize: 12 }}>{p}</Text>
        </Tooltip>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (s: string) => <Tag color={STATUS_COLOR[s] || "default"}>{s}</Tag>,
    },
    { title: "次数", dataIndex: "attempts", width: 70 },
    {
      title: "最近错误",
      dataIndex: "last_error",
      width: 140,
      render: (err: string | null) =>
        err ? (
          <Tooltip title={err}>
            <Text type="danger" ellipsis style={{ maxWidth: 120 }}>{err}</Text>
          </Tooltip>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    { title: "更新时间", dataIndex: "updated_at", width: 170, render: fmtTime },
  ];

  return (
    <div style={{ padding: 20, height: "100%", overflow: "auto" }}>
      <Card size="small" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
        <Row gutter={24} align="middle">
          <Col>
            <Statistic title="事件总数" value={stats?.total ?? 0} />
          </Col>
          <Col>
            <Statistic title="PENDING" value={stats?.PENDING ?? 0}
              valueStyle={stats?.PENDING ? { color: "#faad14" } : undefined} />
          </Col>
          <Col>
            <Statistic title="DONE" value={stats?.DONE ?? 0} />
          </Col>
          <Col>
            <Statistic title="DEAD" value={stats?.DEAD ?? 0}
              valueStyle={stats?.DEAD ? { color: "#cf1322" } : undefined} />
          </Col>
          <Col flex="auto" />
          <Col>
            <Text type="secondary" style={{ fontSize: 12 }}>计数基于最近 200 条</Text>
          </Col>
          <Col>
            <Space>
              <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>刷新</Button>
              <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => setPushOpen(true)}>
                模拟 Push
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Card size="small">
        <Row gutter={12} align="middle" style={{ marginBottom: 12 }}>
          <Col>
            <Space>
              <Text type="secondary" style={{ fontSize: 12 }}>状态</Text>
              <Select
                allowClear
                placeholder="全部状态"
                style={{ width: 140 }}
                value={statusFilter}
                onChange={(v) => setStatusFilter(v)}
                options={STATUS_OPTS}
              />
            </Space>
          </Col>
          <Col flex="auto" />
          <Col>
            <Text type="secondary" style={{ fontSize: 12 }}>
              显示 {events.length} 条 / 20s 自动轮询
            </Text>
          </Col>
        </Row>
        <Table<PipelineEventItem>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={events}
          columns={columns}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 1000 }}
          locale={{ emptyText: "暂无管道事件——推送 webhook 或跑 scripts/ingest_*.py 触发" }}
        />
      </Card>

      <Modal
        title="模拟 Push"
        open={pushOpen}
        onOk={onPush}
        onCancel={() => setPushOpen(false)}
        confirmLoading={pushing}
        okText="入队"
        destroyOnClose
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="repo" label="仓库" rules={[{ required: true, message: "请选择仓库" }]}>
            <Select placeholder="选择 repos_root 下的仓库" options={repos.map((r) => ({ value: r, label: r }))} />
          </Form.Item>
          <Form.Item name="commit_hash" label="提交 hash" rules={[{ required: true, message: "请输入 commit hash" }]}>
            <Input placeholder="如 1cb9fe908d" />
          </Form.Item>
          <Form.Item
            name="files"
            label="变更文件（每行「路径 状态」，如 a/B.java M）"
            rules={[{ required: true, message: "请至少填写一行" }]}
            tooltip="状态取 git 的 A/M/D/R 等；逐行解析为 [{path, status}] 后入队"
          >
            <Input.TextArea rows={5} placeholder={"src/main/java/a/B.java M\nsrc/main/java/a/C.java A"} />
          </Form.Item>
          <Text type="secondary" style={{ fontSize: 12 }}>
            仅入队 Redis Stream（commit_hash+files 形态）；记账与重试由 worker 完成，入队后自动刷新。
          </Text>
        </Form>
      </Modal>
    </div>
  );
}
