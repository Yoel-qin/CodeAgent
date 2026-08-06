/**
 * 同步管理页（Phase 8.4）：同步状态 + 任务列表（含回滚高亮 + 详情抽屉）
 * + 回滚记录 + 触发同步。对齐后端 /v1/sync/* 6 个接口。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  getSyncStatus,
  getSyncTask,
  listRollbacks,
  listSyncTasks,
  triggerSync,
  type ChangeDetailItem,
  type RollbackItem,
  type SyncStatusResponse,
  type SyncTaskDetailResponse,
  type SyncTaskItem,
  type SyncType,
} from "../api/sync";

const { Text, Paragraph } = Typography;

const short = (h: string | null | undefined, n = 8) => (h ? h.slice(0, n) : "—");
const fmtTime = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";
const fmtMs = (ms: number | null | undefined) =>
  ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;

const STATUS_COLOR: Record<string, string> = {
  COMPLETED: "success",
  FAILED: "error",
  RUNNING: "processing",
  PENDING: "default",
};
const TYPE_COLOR: Record<string, string> = { FULL: "blue", INCREMENTAL: "cyan" };
const CHANGE_COLOR: Record<string, string> = {
  ADDED: "green",
  MODIFIED: "blue",
  DELETED: "red",
  RESTORED: "gold",
  ROLLBACK: "purple",
};
const CHANGE_LABEL: Record<string, string> = {
  ADDED: "新增",
  MODIFIED: "修改",
  DELETED: "删除",
  RESTORED: "恢复",
  ROLLBACK: "回滚",
};

function ChangeTags({ c }: { c: { added: number; modified: number; deleted: number } }) {
  return (
    <Space size={4}>
      {c.added > 0 && <Text type="success">+{c.added}</Text>}
      {c.modified > 0 && <Text type="warning">~{c.modified}</Text>}
      {c.deleted > 0 && <Text type="danger">-{c.deleted}</Text>}
      {c.added + c.modified + c.deleted === 0 && <Text type="secondary">0</Text>}
    </Space>
  );
}

const taskColumns = (onDetail: (id: number) => void): ColumnsType<SyncTaskItem> => [
  { title: "#", dataIndex: "task_id", width: 56 },
  {
    title: "类型",
    dataIndex: "type",
    width: 110,
    render: (t: string) => <Tag color={TYPE_COLOR[t] || "default"}>{t}</Tag>,
  },
  {
    title: "提交",
    dataIndex: "commit",
    width: 110,
    render: (h: string) => <Tooltip title={h}>{short(h)}</Tooltip>,
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 110,
    render: (s: string) => <Tag color={STATUS_COLOR[s] || "default"}>{s}</Tag>,
  },
  {
    title: "变更",
    width: 120,
    render: (_, r) => <ChangeTags c={r.changes} />,
  },
  {
    title: "回滚",
    width: 80,
    render: (_, r) =>
      r.source_commit || r.rollback_detail ? (
        <Tooltip title={r.source_commit ? `回退自 ${short(r.source_commit)}` : ""}>
          <Tag color="purple">回滚</Tag>
        </Tooltip>
      ) : (
        <Text type="secondary">—</Text>
      ),
  },
  { title: "耗时", dataIndex: "duration_ms", width: 90, render: fmtMs },
  { title: "开始时间", dataIndex: "started_at", width: 180, render: fmtTime },
  { title: "触发", dataIndex: "triggered_by", width: 90 },
  {
    title: "操作",
    width: 80,
    fixed: "right",
    render: (_, r) => (
      <Button type="link" size="small" onClick={() => onDetail(r.task_id)}>
        详情
      </Button>
    ),
  },
];

const changeColumns: ColumnsType<ChangeDetailItem> = [
  {
    title: "chunk_id",
    dataIndex: "chunk_id",
    render: (c: string) => <Text code style={{ fontSize: 12 }}>{c}</Text>,
  },
  { title: "文件", dataIndex: "file", render: (f: string | null) => f || "—" },
  {
    title: "类型",
    dataIndex: "change_type",
    width: 90,
    render: (t: string) => <Tag color={CHANGE_COLOR[t] || "default"}>{CHANGE_LABEL[t] || t}</Tag>,
  },
  {
    title: "回滚来源",
    dataIndex: "rollback_source_commit",
    width: 120,
    render: (h: string | null) =>
      h ? (
        <Tooltip title={h}>
          <Tag color="purple">{short(h)}</Tag>
        </Tooltip>
      ) : (
        <Text type="secondary">—</Text>
      ),
  },
];

const rollbackColumns: ColumnsType<RollbackItem> = [
  { title: "#", dataIndex: "rollback_id", width: 56 },
  {
    title: "回滚提交",
    dataIndex: "rollback_commit",
    width: 110,
    render: (h: string) => <Tooltip title={h}>{short(h)}</Tooltip>,
  },
  {
    title: "源提交",
    dataIndex: "source_commit",
    width: 110,
    render: (h: string) => <Tooltip title={h}>{short(h)}</Tooltip>,
  },
  { title: "回退", dataIndex: "chunks_rolled_back", width: 70 },
  { title: "恢复", dataIndex: "chunks_restored", width: 70 },
  { title: "删除", dataIndex: "chunks_deleted", width: 70 },
  { title: "关系", dataIndex: "relations_restored", width: 70 },
  { title: "锚点", dataIndex: "anchors_restored", width: 70 },
  { title: "文档PR", dataIndex: "doc_pr_closed", width: 120, render: (v: string | null) => v || "—" },
  { title: "触发", dataIndex: "triggered_by", width: 90 },
  {
    title: "状态",
    dataIndex: "status",
    width: 100,
    render: (s: string) => <Tag color={STATUS_COLOR[s] || "default"}>{s}</Tag>,
  },
  { title: "时间", dataIndex: "created_at", width: 180, render: fmtTime },
];

export default function SyncPage() {
  const [status, setStatus] = useState<SyncStatusResponse | null>(null);
  const [tasks, setTasks] = useState<SyncTaskItem[]>([]);
  const [rollbacks, setRollbacks] = useState<RollbackItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<SyncTaskDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [form] = Form.useForm<{ type: SyncType; target_commit?: string }>();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, t, r] = await Promise.all([getSyncStatus(), listSyncTasks({ page_size: 50 }), listRollbacks({ page_size: 50 })]);
      setStatus(s);
      setTasks(t.items);
      setRollbacks(r.items);
    } catch (e) {
      message.error((e as Error).message || "加载同步数据失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 20_000);
    return () => clearInterval(t);
  }, [refresh]);

  const openDetail = async (id: number) => {
    setDetailLoading(true);
    try {
      setDetail(await getSyncTask(id));
    } catch (e) {
      message.error((e as Error).message || "加载任务详情失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const onTrigger = async () => {
    const values = await form.validateFields();
    setTriggering(true);
    try {
      const res = await triggerSync({
        type: values.type,
        target_commit: values.target_commit?.trim() || undefined,
      });
      if (res.status === "COMPLETED") message.success(res.message);
      else message.warning(res.message);
      setTriggerOpen(false);
      form.resetFields();
      refresh();
    } catch (e) {
      message.error((e as Error).message || "触发同步失败");
    } finally {
      setTriggering(false);
    }
  };

  const stats = status?.stats;
  const st = status?.status;
  const ok = st === "HEALTHY";

  return (
    <div style={{ padding: 20, height: "100%", overflow: "auto" }}>
      <Card
        size="small"
        styles={{ body: { padding: "12px 20px" } }}
        style={{ marginBottom: 16 }}
      >
        <Row gutter={24} align="middle">
          <Col>
            <Statistic title="代码 chunk" value={stats?.code_chunks ?? 0} />
          </Col>
          <Col>
            <Statistic title="文档 chunk" value={stats?.doc_chunks ?? 0} />
          </Col>
          <Col>
            <Statistic title="过期文档" value={stats?.stale_docs ?? 0} valueStyle={stats?.stale_docs ? { color: "#fa8c16" } : undefined} />
          </Col>
          <Col>
            <Statistic title="关联关系" value={stats?.total_relations ?? 0} />
          </Col>
          <Col>
            <Statistic title="锚点" value={stats?.total_anchors ?? 0} />
          </Col>
          <Col flex="auto" />
          <Col>
            <Space direction="vertical" size={0} align="end">
              <Tag color={ok ? "success" : "warning"}>{ok ? "● 索引正常" : "● 待关注"}</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>
                最近同步：{fmtTime(status?.last_sync_at)}　{status?.last_commit ? `@${short(status.last_commit)}` : ""}
              </Text>
            </Space>
          </Col>
          <Col>
            <Space>
              <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>
                刷新
              </Button>
              <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => setTriggerOpen(true)}>
                触发同步
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Card size="small">
        <Tabs
          defaultActiveKey="tasks"
          items={[
            {
              key: "tasks",
              label: `同步任务 (${tasks.length})`,
              children: (
                <Table<SyncTaskItem>
                  rowKey="task_id"
                  size="small"
                  loading={loading}
                  dataSource={tasks}
                  columns={taskColumns(openDetail)}
                  pagination={{ pageSize: 10, showSizeChanger: false }}
                  scroll={{ x: 980 }}
                  rowClassName={(r) => (r.source_commit || r.rollback_detail ? "coderag-rollback-row" : "")}
                />
              ),
            },
            {
              key: "rollbacks",
              label: `回滚记录 (${rollbacks.length})`,
              children: (
                <Table<RollbackItem>
                  rowKey="rollback_id"
                  size="small"
                  loading={loading}
                  dataSource={rollbacks}
                  columns={rollbackColumns}
                  pagination={{ pageSize: 10, showSizeChanger: false }}
                  scroll={{ x: 1080 }}
                />
              ),
            },
          ]}
        />
      </Card>

      <Drawer
        title={`同步任务 #${detail?.task_id ?? ""} 详情`}
        open={!!detail}
        onClose={() => setDetail(null)}
        width={680}
        loading={detailLoading}
        destroyOnClose
      >
        {detail && (
          <>
            <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="类型">
                <Tag color={TYPE_COLOR[detail.type] || "default"}>{detail.type}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLOR[detail.status] || "default"}>{detail.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="提交" span={2}>
                <Text code>{detail.commit}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="开始">{fmtTime(detail.started_at)}</Descriptions.Item>
              <Descriptions.Item label="完成">{fmtTime(detail.finished_at)}</Descriptions.Item>
              <Descriptions.Item label="耗时">{fmtMs(detail.duration_ms)}</Descriptions.Item>
              <Descriptions.Item label="触发">{detail.triggered_by || "—"}</Descriptions.Item>
              {detail.source_commit && (
                <Descriptions.Item label="回滚源" span={2}>
                  <Tag color="purple">{detail.source_commit}</Tag>
                </Descriptions.Item>
              )}
              {detail.rollback_detail && (
                <Descriptions.Item label="回滚明细" span={2}>
                  <Space size={12} wrap>
                    <Text>回退 {detail.rollback_detail.chunks_rolled_back}</Text>
                    <Text type="success">恢复 {detail.rollback_detail.chunks_restored}</Text>
                    <Text>关系 {detail.rollback_detail.relations_restored}</Text>
                    <Text>锚点 {detail.rollback_detail.anchors_restored}</Text>
                  </Space>
                </Descriptions.Item>
              )}
            </Descriptions>

            <Typography.Title level={5}>变更明细 ({detail.change_details.length})</Typography.Title>
            <Table<ChangeDetailItem>
              rowKey={(r) => `${r.chunk_id}-${r.change_type}`}
              size="small"
              dataSource={detail.change_details}
              columns={changeColumns}
              pagination={{ pageSize: 8, showSizeChanger: false }}
              scroll={{ x: 520 }}
              style={{ marginBottom: 16 }}
            />

            {detail.errors?.length > 0 && (
              <>
                <Typography.Title level={5}>错误 ({detail.errors.length})</Typography.Title>
                {detail.errors.map((e, i) => (
                  <Paragraph key={i} type="danger" style={{ fontSize: 12, marginBottom: 4 }}>
                    {e.file ? `[${e.file}] ` : ""}
                    {e.error}
                  </Paragraph>
                ))}
              </>
            )}
          </>
        )}
      </Drawer>

      <Modal
        title="触发同步"
        open={triggerOpen}
        onOk={onTrigger}
        onCancel={() => setTriggerOpen(false)}
        confirmLoading={triggering}
        okText="开始同步"
        destroyOnClose
      >
        <Form form={form} layout="vertical" initialValues={{ type: "INCREMENTAL" }} preserve={false}>
          <Form.Item name="type" label="同步类型" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "INCREMENTAL", label: "INCREMENTAL（增量：git diff → 仅变更文件）" },
                { value: "FULL", label: "FULL（全量：重新解析整个仓库 + 重建关联/调用图）" },
              ]}
            />
          </Form.Item>
          <Form.Item name="target_commit" label="目标提交（可选，缺省取 HEAD）" tooltip="同步到该 git 提交；留空则取当前 HEAD">
            <Input placeholder="如 1cb9fe90 或分支/tag" />
          </Form.Item>
          <Text type="secondary" style={{ fontSize: 12 }}>
            增量同步在无已完成游标时会自动回退为全量。同步为阻塞执行，完成后自动刷新。
          </Text>
        </Form>
      </Modal>
    </div>
  );
}
