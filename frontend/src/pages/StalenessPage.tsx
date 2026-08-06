/**
 * 腐化审批页（Phase 7 Milestone 19）：DOC↔CODE 过时关系报告 + 重写提案审批队列。
 * 对齐后端 /v1/staleness/* 4 个接口（M16 报告 / M17 SWEEP 批量重写 / M18 审批写回）。
 * 模板：SyncPage（KPI 统计行 + 行内详情抽屉 + 触发动作 Modal + 20s 轮询 + 状态 Tag）。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  InputNumber,
  Modal,
  Popconfirm,
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
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  decideProposal,
  getStalenessReport,
  listStalenessProposals,
  runSweepRewrite,
  type ProposalDecision,
  type ProposalItem,
  type StalenessFinding,
  type StalenessReport,
} from "../api/staleness";

const { Text } = Typography;

const fmtTime = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";
const short = (h: string | null | undefined, n = 10) => (h ? h.slice(0, n) : "—");

/** 提案状态色：待审蓝/黄、已决定绿/灰、失败红。 */
const PROPOSAL_STATUS_COLOR: Record<string, string> = {
  PENDING_PUSH: "processing",
  PENDING_MANUAL: "warning",
  APPROVED: "success",
  REJECTED: "default",
  FAILED: "error",
  MERGED: "success",
  CLOSED_BY_ROLLBACK: "default",
};
const RELATION_TYPE_COLOR: Record<string, string> = {
  DOC_TO_CODE: "blue",
  CODE_TO_DOC: "geekblue",
  CODE_CALLS_CODE: "cyan",
};
/** active（待审、占位）= PENDING_PUSH / PENDING_MANUAL；可审批。 */
const isPending = (s: string | null | undefined) => s === "PENDING_PUSH" || s === "PENDING_MANUAL";

const STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "PENDING_PUSH", label: "PENDING_PUSH（待推送）" },
  { value: "PENDING_MANUAL", label: "PENDING_MANUAL（待人工）" },
  { value: "APPROVED", label: "APPROVED（已通过）" },
  { value: "REJECTED", label: "REJECTED（已驳回）" },
  { value: "FAILED", label: "FAILED（失败）" },
];

const recentColumns: ColumnsType<StalenessFinding> = [
  { title: "#", dataIndex: "relation_id", width: 64 },
  {
    title: "关系类型",
    dataIndex: "relation_type",
    width: 130,
    render: (t: string | null) =>
      t ? <Tag color={RELATION_TYPE_COLOR[t] || "default"}>{t}</Tag> : <Text type="secondary">—</Text>,
  },
  {
    title: "锚点",
    dataIndex: "anchor_key",
    ellipsis: true,
    render: (a: string | null) =>
      a ? (
        <Tooltip title={a}>
          <Text style={{ fontSize: 12 }}>{a}</Text>
        </Tooltip>
      ) : (
        <Text type="secondary">—</Text>
      ),
  },
  {
    title: "过时原因",
    dataIndex: "stale_reason",
    ellipsis: true,
    render: (r: string | null) =>
      r ? (
        <Tooltip title={r}>
          <Tag color={r.startsWith("SWEEP:") ? "orange" : r.startsWith("DELETED:") ? "red" : "default"}>
            {r}
          </Tag>
        </Tooltip>
      ) : (
        <Text type="secondary">—</Text>
      ),
  },
  { title: "更新时间", dataIndex: "updated_at", width: 180, render: fmtTime },
];

const proposalColumns = (onOpen: (r: ProposalItem) => void): ColumnsType<ProposalItem> => [
  { title: "#", dataIndex: "proposal_id", width: 64 },
  {
    title: "doc chunk",
    dataIndex: "doc_chunk_id",
    width: 150,
    render: (c: string | null) =>
      c ? (
        <Tooltip title={c}>
          <Text code style={{ fontSize: 12 }}>
            {short(c, 14)}
          </Text>
        </Tooltip>
      ) : (
        <Text type="secondary">—</Text>
      ),
  },
  {
    title: "章节",
    dataIndex: "heading_path",
    ellipsis: true,
    render: (h: string[] | null) => (h && h.length ? h.join(" / ") : <Text type="secondary">—</Text>),
  },
  {
    title: "锚点关系",
    dataIndex: "relation_ids",
    width: 120,
    render: (ids: number[] | null) =>
      ids && ids.length ? ids.join(", ") : <Text type="secondary">—</Text>,
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 130,
    render: (s: string | null) => (s ? <Tag color={PROPOSAL_STATUS_COLOR[s] || "default"}>{s}</Tag> : "—"),
  },
  {
    title: "已重写",
    dataIndex: "rewritten_ok",
    width: 90,
    render: (ok: boolean) => (ok ? <Tag color="success">是</Tag> : <Tag color="warning">否</Tag>),
  },
  {
    title: "分支",
    dataIndex: "branch_name",
    width: 130,
    ellipsis: true,
    render: (b: string | null) =>
      b ? (
        <Tooltip title={b}>
          <Text style={{ fontSize: 12 }}>{b}</Text>
        </Tooltip>
      ) : (
        <Text type="secondary">—</Text>
      ),
  },
  { title: "创建时间", dataIndex: "created_at", width: 180, render: fmtTime },
  {
    title: "操作",
    width: 110,
    fixed: "right",
    render: (_, r) => (
      <Button type="link" size="small" onClick={() => onOpen(r)}>
        {isPending(r.status) ? "审批" : "详情"}
      </Button>
    ),
  },
];

export default function StalenessPage() {
  const [report, setReport] = useState<StalenessReport | null>(null);
  const [items, setItems] = useState<ProposalItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");
  const [sweepOpen, setSweepOpen] = useState(false);
  const [sweepLoading, setSweepLoading] = useState(false);
  const [sweepTopN, setSweepTopN] = useState(10);
  const [selected, setSelected] = useState<ProposalItem | null>(null);
  const [deciding, setDeciding] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [rep, list] = await Promise.all([
        getStalenessReport(20),
        listStalenessProposals({ status: statusFilter || undefined, page_size: 100 }),
      ]);
      setReport(rep);
      setItems(list.items);
      setTotal(list.total);
    } catch (e) {
      message.error((e as Error).message || "加载腐化审批数据失败");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 20_000);
    return () => clearInterval(t);
  }, [refresh]);

  const onSweep = async () => {
    setSweepLoading(true);
    try {
      const res = await runSweepRewrite({ top_n: sweepTopN });
      if (res.error) {
        message.warning(`巡检完成但出错：${res.error}`);
      } else {
        message.success(
          `巡检 ${res.scanned} 条关系 / ${res.slots} 段落，重写 ${res.rewritten}，待审 ${res.pending_push + res.pending_manual}（跳过 ${res.skipped_existing}）`,
        );
      }
      setSweepOpen(false);
      refresh();
    } catch (e) {
      message.error((e as Error).message || "触发巡检失败");
    } finally {
      setSweepLoading(false);
    }
  };

  const onDecide = async (decision: ProposalDecision) => {
    if (!selected?.proposal_id) return;
    setDeciding(true);
    try {
      const res = await decideProposal(selected.proposal_id, decision);
      if (decision === "APPROVED" && res.applied) {
        const embedHint =
          res.reembed_status === "synced"
            ? "；已即时重嵌入向量库"
            : res.reembed_status === "failed"
              ? "；即时重嵌入失败，将由重嵌入任务补"
              : res.reembed_status === "lazy"
                ? "；待重嵌入"
                : "";
        const gitHint =
          res.git_status === "PUSHED"
            ? `；已推送 PR${res.pr_url ? `（${res.pr_url}）` : ""}`
            : res.git_status === "COMMITTED"
              ? "；已提交到本地分支"
              : res.git_status === "PUSH_FAILED"
                ? "；PR 创建失败（KB 已更新，可重试）"
                : "";
        message.success(
          `已通过：写回 ${short(res.doc_chunk_id)}…，清理 ${res.relations_cleared} 条关系${embedHint}${gitHint}`,
        );
      } else if (decision === "APPROVED") {
        message.success("已通过");
      } else {
        message.success("已驳回");
      }
      setSelected(null);
      refresh();
    } catch (e) {
      message.error((e as Error).message || "审批失败");
    } finally {
      setDeciding(false);
    }
  };

  const pending = isPending(selected?.status);

  return (
    <div style={{ padding: 20, height: "100%", overflow: "auto" }}>
      <Card size="small" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
        <Row gutter={24} align="middle">
          <Col>
            <Statistic title="DOC↔CODE 关系" value={report?.total ?? 0} />
          </Col>
          <Col>
            <Statistic
              title="过时"
              value={report?.stale ?? 0}
              valueStyle={report?.stale ? { color: "#fa8c16" } : undefined}
            />
          </Col>
          <Col>
            <Statistic title="SWEEP 巡检" value={report?.by_source?.sweep ?? 0} />
          </Col>
          <Col>
            <Statistic title="代码删除" value={report?.by_source?.deleted ?? 0} />
          </Col>
          <Col>
            <Statistic title="其他来源" value={report?.by_source?.other ?? 0} />
          </Col>
          <Col flex="auto" />
          <Col>
            <Space>
              <Select
                value={statusFilter}
                onChange={setStatusFilter}
                options={STATUS_OPTIONS}
                style={{ width: 220 }}
              />
              <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>
                刷新
              </Button>
              <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => setSweepOpen(true)}>
                巡检并生成提案
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Card size="small" title={`最近过时发现 (${report?.recent?.length ?? 0})`} style={{ marginBottom: 16 }}>
        <Table<StalenessFinding>
          rowKey="relation_id"
          size="small"
          loading={loading}
          dataSource={report?.recent ?? []}
          columns={recentColumns}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          scroll={{ x: 760 }}
          locale={{ emptyText: <Empty description="暂无 SWEEP 巡检发现" /> }}
        />
      </Card>

      <Card size="small" title={`提案审批队列 (${total})`}>
        <Table<ProposalItem>
          rowKey="proposal_id"
          size="small"
          loading={loading}
          dataSource={items}
          columns={proposalColumns(setSelected)}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 1080 }}
          locale={{ emptyText: <Empty description="暂无提案" /> }}
        />
      </Card>

      <Drawer
        title={`提案 #${selected?.proposal_id ?? ""}`}
        open={!!selected}
        onClose={() => setSelected(null)}
        width={780}
        destroyOnClose
      >
        {selected && (
          <>
            <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="doc chunk" span={2}>
                <Text code style={{ fontSize: 12 }}>
                  {selected.doc_chunk_id || "—"}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="章节" span={2}>
                {selected.heading_path?.length ? selected.heading_path.join(" / ") : "—"}
              </Descriptions.Item>
              <Descriptions.Item label="锚点关系">
                {selected.relation_ids?.length ? selected.relation_ids.join(", ") : "—"}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={PROPOSAL_STATUS_COLOR[selected.status || ""] || "default"}>
                  {selected.status || "—"}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="分支">
                {selected.branch_name || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="已重写">
                {selected.rewritten_ok ? <Tag color="success">是</Tag> : <Tag color="warning">否</Tag>}
              </Descriptions.Item>
              {selected.commit_sha && (
                <Descriptions.Item label="提交" span={2}>
                  <Tooltip title={selected.commit_sha}>
                    <Text code style={{ fontSize: 12 }}>
                      {short(selected.commit_sha, 12)}
                    </Text>
                  </Tooltip>
                </Descriptions.Item>
              )}
              {selected.pr_url && (
                <Descriptions.Item label="PR 链接" span={2}>
                  <Tooltip title={selected.pr_url}>
                    <Text style={{ fontSize: 12, wordBreak: "break-all" }}>{selected.pr_url}</Text>
                  </Tooltip>
                </Descriptions.Item>
              )}
              {selected.artifact_key && (
                <Descriptions.Item label="MinIO 工件" span={2}>
                  <Tooltip title={selected.artifact_key}>
                    <Text code style={{ fontSize: 12 }}>
                      {short(selected.artifact_key, 48)}
                    </Text>
                  </Tooltip>
                </Descriptions.Item>
              )}
            </Descriptions>

            <Typography.Title level={5}>内容预览</Typography.Title>
            <Row gutter={16}>
              <Col span={12}>
                <Text strong>原文</Text>
                <div
                  className="coderag-md"
                  style={{ marginTop: 8, padding: 12, background: "#fafafa", borderRadius: 6, maxHeight: 360, overflow: "auto" }}
                >
                  {selected.original_text ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.original_text}</ReactMarkdown>
                  ) : (
                    <Empty description="无原文快照" />
                  )}
                </div>
              </Col>
              <Col span={12}>
                <Text strong>重写后</Text>
                <div
                  className="coderag-md"
                  style={{ marginTop: 8, padding: 12, background: "#f6ffed", borderRadius: 6, maxHeight: 360, overflow: "auto" }}
                >
                  {selected.rewritten_text ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.rewritten_text}</ReactMarkdown>
                  ) : (
                    <Empty description="无重写（需人工 / LLM 未配置，通过将被拒）" />
                  )}
                </div>
              </Col>
            </Row>

            <div
              style={{
                marginTop: 20,
                paddingTop: 12,
                borderTop: "1px solid #f0f0f0",
                display: "flex",
                justifyContent: "flex-end",
              }}
            >
              {pending ? (
                <Space>
                  <Popconfirm
                    title="确认驳回该提案？"
                    onConfirm={() => onDecide("REJECTED")}
                    okText="驳回"
                    cancelText="取消"
                  >
                    <Button danger loading={deciding}>
                      驳回
                    </Button>
                  </Popconfirm>
                  <Popconfirm
                    title="确认通过？将写回 doc_chunks 内容并清理过时关系。"
                    onConfirm={() => onDecide("APPROVED")}
                    okText="通过并写回"
                    cancelText="取消"
                  >
                    <Button type="primary" loading={deciding}>
                      通过并写回
                    </Button>
                  </Popconfirm>
                </Space>
              ) : (
                <Text type="secondary">该提案已审批（{selected.status}），不可重复操作。</Text>
              )}
            </div>
          </>
        )}
      </Drawer>

      <Modal
        title="巡检并生成提案"
        open={sweepOpen}
        onOk={onSweep}
        onCancel={() => setSweepOpen(false)}
        confirmLoading={sweepLoading}
        okText="开始巡检"
        destroyOnClose
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <div>
            <Text>为前 N 条 SWEEP 标记的过时文档段落批量生成重写提案（落审批队列）：</Text>
          </div>
          <div>
            <Text style={{ marginRight: 12 }}>Top-N（1–50）</Text>
            <InputNumber
              min={1}
              max={50}
              value={sweepTopN}
              onChange={(v) => setSweepTopN(Number(v) || 10)}
              style={{ width: 120 }}
            />
          </div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            复用 M15 重写原语（LLM 重写 + MinIO 工件）；已有待审提案的段落会被幂等跳过。完成后自动刷新。
          </Text>
        </Space>
      </Modal>
    </div>
  );
}
