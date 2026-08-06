/**
 * Agent 面板页（Phase 7 Milestone 7）：聚合 KPI（调用/满意度/平均步骤/降级率）
 * + 各 Agent 明细表 + 最近运行流水。对齐后端 /v1/agents/stats、/v1/agents/runs。
 * 数据全量来自 retrieval_logs 按需聚合（无新表）；mode='agent' = Agent 成功跑完。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Card, Col, Row, Select, Space, Statistic, Table, Tag, Tooltip, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  getAgentStats,
  listAgentRuns,
  type AgentRunItem,
  type AgentStatRow,
  type AgentStats,
  type AgentWindow,
} from "../api/agents";

const { Text } = Typography;

const AGENT_LABEL: Record<string, string> = {
  CODE_UNDERSTAND: "代码理解",
  DOC_ANSWER: "文档问答",
  CHANGE_IMPACT: "变更影响",
  BUG_DIAGNOSIS: "缺陷诊断",
};
const AGENT_COLOR: Record<string, string> = {
  CODE_UNDERSTAND: "blue",
  DOC_ANSWER: "cyan",
  CHANGE_IMPACT: "purple",
  BUG_DIAGNOSIS: "magenta",
};

const fmtTime = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(0)}%`);
const num1 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(1));

const AgentTag = ({ a }: { a: string | null }) =>
  a ? (
    <Tag color={AGENT_COLOR[a] || "default"}>{AGENT_LABEL[a] || a}</Tag>
  ) : (
    <Tag color="default">未归属</Tag>
  );

const FeedbackTag = ({ f }: { f: string | null | undefined }) => {
  if (f === "HELPFUL") return <Tag color="success">有用</Tag>;
  if (f === "NOT_HELPFUL") return <Tag color="error">无用</Tag>;
  return <Text type="secondary">—</Text>;
};

const perAgentColumns: ColumnsType<AgentStatRow> = [
  { title: "Agent", dataIndex: "agent", width: 120, render: (a: string) => <AgentTag a={a} /> },
  { title: "调用", dataIndex: "calls", width: 80 },
  { title: "平均步骤", dataIndex: "avg_steps", width: 100, render: num1 },
  { title: "命中率", dataIndex: "hit_rate", width: 100, render: pct },
  { title: "满意度", dataIndex: "satisfaction", width: 100, render: pct },
  {
    title: "降级",
    dataIndex: "degraded",
    width: 80,
    render: (n: number) => (n > 0 ? <Tag color="warning">{n}</Tag> : <Text type="secondary">0</Text>),
  },
];

const runColumns: ColumnsType<AgentRunItem> = [
  { title: "时间", dataIndex: "created_at", width: 170, render: fmtTime },
  { title: "Agent", dataIndex: "agent", width: 110, render: (a: string | null) => <AgentTag a={a} /> },
  {
    title: "查询",
    dataIndex: "query",
    ellipsis: true,
    render: (q: string) => (
      <Tooltip title={q}>
        <Text style={{ fontSize: 12 }}>{q}</Text>
      </Tooltip>
    ),
  },
  { title: "步骤", dataIndex: "steps", width: 70 },
  { title: "引用", dataIndex: "citations", width: 70 },
  {
    title: "降级",
    dataIndex: "degraded",
    width: 80,
    render: (d: boolean) => (d ? <Tag color="warning">降级</Tag> : <Tag color="success">正常</Tag>),
  },
  { title: "反馈", dataIndex: "feedback", width: 80, render: (f: string | null) => <FeedbackTag f={f} /> },
];

export default function AgentsPage() {
  const [stats, setStats] = useState<AgentStats | null>(null);
  const [runs, setRuns] = useState<AgentRunItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [timeWindow, setTimeWindow] = useState<AgentWindow>("today");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, r] = await Promise.all([getAgentStats(timeWindow), listAgentRuns({ page_size: 50 })]);
      setStats(s);
      setRuns(r.items);
    } catch (e) {
      message.error((e as Error).message || "加载 Agent 面板数据失败");
    } finally {
      setLoading(false);
    }
  }, [timeWindow]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 20_000);
    return () => clearInterval(t);
  }, [refresh]);

  const degRate = stats?.degradation_rate ?? null;

  return (
    <div style={{ padding: 20, height: "100%", overflow: "auto" }}>
      <Card size="small" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
        <Row gutter={24} align="middle">
          <Col>
            <Statistic title="Agent 调用" value={stats?.total_calls ?? 0} />
          </Col>
          <Col>
            <Statistic title="满意度" value={pct(stats?.satisfaction ?? null)} />
          </Col>
          <Col>
            <Statistic title="平均步骤" value={num1(stats?.avg_steps ?? null)} />
          </Col>
          <Col>
            <Statistic
              title="降级率"
              value={pct(degRate)}
              valueStyle={degRate && degRate > 0 ? { color: "#fa8c16" } : undefined}
            />
          </Col>
          <Col flex="auto" />
          <Col>
            <Space direction="vertical" size={0} align="end">
              <Text type="secondary" style={{ fontSize: 12 }}>
                介入 {stats?.engaged ?? 0} · 降级 {stats?.degraded ?? 0} · 反馈 {stats?.helpful ?? 0}/
                {stats?.feedback ?? 0}
              </Text>
            </Space>
          </Col>
          <Col>
            <Space>
              <Select<AgentWindow>
                value={timeWindow}
                onChange={setTimeWindow}
                style={{ width: 110 }}
                options={[
                  { value: "today", label: "今日" },
                  { value: "7d", label: "近 7 天" },
                  { value: "all", label: "全部" },
                ]}
              />
              <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>
                刷新
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Card size="small" title="各 Agent 明细" style={{ marginBottom: 16 }}>
        <Table<AgentStatRow>
          rowKey="agent"
          size="small"
          loading={loading}
          dataSource={stats?.per_agent ?? []}
          columns={perAgentColumns}
          pagination={false}
        />
      </Card>

      <Card size="small" title={`最近运行 (${runs.length})`}>
        <Table<AgentRunItem>
          rowKey="log_id"
          size="small"
          loading={loading}
          dataSource={runs}
          columns={runColumns}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 760 }}
        />
      </Card>
    </div>
  );
}
