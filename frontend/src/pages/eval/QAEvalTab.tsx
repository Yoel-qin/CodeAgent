/**
 * QA / 幻觉评判 Tab（M39）：LLM 评判答案质量 + 引用幻觉。
 * 镜像 AbEvalTab 结构：控件 + KPI + Sparkline + 历史表 + Drawer（per_query 5 维分）。
 * 对齐后端 /v1/eval/qa（POST）+ /v1/eval/qa-runs（GET 列表）+ /v1/eval/qa-runs/{id}（GET 详情）。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
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
import { PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import Sparkline from "../../components/Sparkline";
import {
  getQARun,
  listQARuns,
  runQA,
  type QAPerQueryRow,
  type QARunDetail,
  type QARunSummary,
} from "../../api/eval";

const { Text, Paragraph, Title } = Typography;

// ---- 格式化（复用 EvalPage 同名工具）----
const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString());
const ratio = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const fmtMs = (ms: number | null | undefined) =>
  ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
const fmtTime = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";

const STATUS_COLOR: Record<string, string> = {
  COMPLETED: "success",
  FAILED: "error",
  PENDING: "default",
};
const STRATEGY_COLOR: Record<string, string> = { unified: "blue", dual: "geekblue" };

const TOPK_OPTIONS = [8, 10, 15, 20].map((k) => ({ value: k, label: `top_k=${k}` }));
const REWRITE_OPTIONS = [
  { value: "off", label: "改写: off" },
  { value: "auto", label: "改写: auto" },
];

// 5 维 judge_scores 的方向标注：high_good = 分高好，low_bad = 分低好（unverified_rate）
const DIM_META: Record<string, { label: string; direction: "high_good" | "low_bad" }> = {
  relevance: { label: "相关性", direction: "high_good" },
  faithfulness: { label: "忠实度", direction: "high_good" },
  completeness: { label: "完整性", direction: "high_good" },
  clarity: { label: "清晰度", direction: "high_good" },
  unverified_rate: { label: "未验证率", direction: "low_bad" },
};

// 维度列表（固定顺序，用于 KPI 行 + per_query 列）
const DIM_KEYS = ["relevance", "faithfulness", "completeness", "clarity", "unverified_rate"];

// ---- 历史表列 ----
const qaHistoryColumns = (onDetail: (id: number) => void): ColumnsType<QARunSummary> => [
  { title: "#", dataIndex: "run_id", width: 56 },
  {
    title: "状态",
    dataIndex: "status",
    width: 110,
    render: (s: string) => <Tag color={STATUS_COLOR[s] || "default"}>{s}</Tag>,
  },
  {
    title: "策略",
    dataIndex: "embedding_strategy",
    width: 90,
    render: (s: string) => <Tag color={STRATEGY_COLOR[s] || "default"}>{s}</Tag>,
  },
  { title: "top_k", dataIndex: "top_k", width: 70 },
  { title: "改写", dataIndex: "rewrite", width: 70 },
  {
    title: "可评测",
    width: 100,
    render: (_, r) => `${num(r.n_evaluable)}/${num(r.n_queries)}`,
  },
  {
    title: "加权质量",
    width: 110,
    render: (_, r) =>
      r.aggregate?.weighted_quality == null ? "—" : r.aggregate.weighted_quality.toFixed(3),
  },
  { title: "耗时", dataIndex: "duration_ms", width: 90, render: fmtMs },
  { title: "时间", dataIndex: "created_at", width: 170, render: fmtTime },
  {
    title: "操作",
    width: 80,
    fixed: "right",
    render: (_, r) => (
      <Button type="link" size="small" onClick={() => onDetail(r.run_id)}>
        详情
      </Button>
    ),
  },
];

// ---- per_query 表列 ----
const perQueryColumns: ColumnsType<QAPerQueryRow> = [
  { title: "ID", dataIndex: "id", width: 60 },
  {
    title: "查询",
    dataIndex: "text",
    ellipsis: true,
    width: 140,
  },
  {
    title: "答案摘要",
    dataIndex: "answer",
    ellipsis: true,
    width: 200,
    render: (a: string) => (
      <Tooltip title={a}>
        <Text style={{ fontSize: 12 }}>{a.length > 80 ? a.slice(0, 80) + "…" : a}</Text>
      </Tooltip>
    ),
  },
  ...DIM_KEYS.map(
    (dim): ColumnsType<QAPerQueryRow>[number] => ({
      title: DIM_META[dim]?.label ?? dim,
      width: 90,
      render: (_, r) => {
        const v = r.judge_scores[dim];
        if (v == null) return <Text type="secondary">—</Text>;
        const meta = DIM_META[dim];
        const color =
          meta?.direction === "low_bad"
            ? v <= 0.2
              ? "success"
              : v >= 0.8
                ? "danger"
                : "warning"
            : v >= 0.8
              ? "success"
              : v <= 0.4
                ? "danger"
                : "warning";
        return <Text type={color}>{v.toFixed(2)}</Text>;
      },
    }),
  ),
  {
    title: "加权分",
    dataIndex: "weighted_score",
    width: 80,
    render: (v: number | null) => (v == null ? "—" : v.toFixed(3)),
  },
  {
    title: "错误",
    dataIndex: "error",
    width: 80,
    render: (e: string | null) =>
      e ? (
        <Tooltip title={e}>
          <Text type="danger" style={{ fontSize: 12 }}>
            有
          </Text>
        </Tooltip>
      ) : (
        <Text type="secondary">—</Text>
      ),
  },
];

export default function QAEvalTab() {
  const [history, setHistory] = useState<QARunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [topK, setTopK] = useState(8);
  const [rewrite, setRewrite] = useState<"off" | "auto">("off");
  const [detail, setDetail] = useState<QARunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listQARuns(50);
      setHistory(data.items);
    } catch (e) {
      message.error((e as Error).message || "加载 QA 评判历史失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const openDetail = async (id: number) => {
    setDetailLoading(true);
    try {
      setDetail(await getQARun(id));
    } catch (e) {
      message.error((e as Error).message || "加载 QA 评判详情失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const onRun = async () => {
    setTriggering(true);
    try {
      const res = await runQA({ top_k: topK, rewrite });
      if (res.status === "COMPLETED") {
        const wq = res.aggregate?.weighted_quality?.toFixed(3) ?? "—";
        message.success(`QA 评判完成：加权质量 ${wq} · ${res.n_evaluable}/${res.n_queries} 可评测`);
      } else {
        message.warning(`QA 评判未完成：${res.error_message || res.status}`);
      }
      await refresh();
    } catch (e) {
      message.error((e as Error).message || "运行 QA 评判失败");
    } finally {
      setTriggering(false);
    }
  };

  const latest = history[0]?.aggregate ?? null;
  // 趋势：历史最新在前 → reverse 旧→新
  const trendWQ = history.map((h) => h.aggregate?.weighted_quality ?? null).reverse();
  // per-dimension trends
  const dimTrends = DIM_KEYS.map((dim) => history.map((h) => h.aggregate?.means[dim] ?? null).reverse());
  const DIM_COLORS = ["#1677ff", "#52c41a", "#722ed1", "#fa8c16", "#f5222d"];

  return (
    <>
      {/* 控件栏 */}
      <Card size="small" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col>
            <Select value={topK} onChange={setTopK} style={{ width: 90 }} options={TOPK_OPTIONS} />
          </Col>
          <Col>
            <Select value={rewrite} onChange={setRewrite} style={{ width: 120 }} options={REWRITE_OPTIONS} />
          </Col>
          <Col flex="auto" />
          <Col>
            <Space>
              <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>
                刷新
              </Button>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={onRun}
                loading={triggering}
              >
                运行评判
              </Button>
            </Space>
          </Col>
        </Row>
        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          LLM 评判生成答案的相关性、忠实度、完整性、清晰度（1-5 分）+ 未验证率（幻觉信号），
          加权质量 = 4 维加权均值。每 query 走真实检索→生成→评判全链路。
        </Paragraph>
      </Card>

      {/* KPI 卡 */}
      <Card size="small" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          {DIM_KEYS.map((dim) => (
            <Col key={dim}>
              <Statistic
                title={DIM_META[dim]?.label ?? dim}
                value={
                  latest?.means[dim] == null
                    ? "—"
                    : latest.means[dim]!.toFixed(2)
                }
              />
            </Col>
          ))}
          <Col>
            <Statistic
              title="加权质量"
              value={
                latest?.weighted_quality == null
                  ? "—"
                  : latest.weighted_quality.toFixed(3)
              }
            />
          </Col>
        </Row>
      </Card>

      {/* 趋势 Sparkline */}
      <Card
        size="small"
        title="质量趋势（历史，旧 → 新）"
        styles={{ body: { padding: 16 } }}
        style={{ marginBottom: 16 }}
      >
        <Row gutter={24}>
          <Col>
            <Statistic
              title="加权质量趋势"
              valueRender={() => <Sparkline values={trendWQ} color="#1677ff" />}
            />
          </Col>
          {DIM_KEYS.map((dim, i) => (
            <Col key={dim}>
              <Statistic
                title={`${DIM_META[dim]?.label ?? dim} 趋势`}
                valueRender={() => <Sparkline values={dimTrends[i]} color={DIM_COLORS[i]} />}
              />
            </Col>
          ))}
        </Row>
      </Card>

      {/* 历史表 */}
      <Card size="small" title={`QA 评判历史 (${history.length})`} style={{ marginBottom: 16 }}>
        <Table<QARunSummary>
          rowKey="run_id"
          size="small"
          loading={loading}
          dataSource={history}
          columns={qaHistoryColumns(openDetail)}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 1000 }}
        />
      </Card>

      {/* 详情 Drawer */}
      <Drawer
        title={`QA 评判 #${detail?.run_id ?? ""} 详情`}
        open={!!detail}
        onClose={() => setDetail(null)}
        width={960}
        loading={detailLoading}
        destroyOnClose
      >
        {detail && (
          <>
            <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLOR[detail.status] || "default"}>{detail.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="策略">
                <Tag color={STRATEGY_COLOR[detail.embedding_strategy] || "default"}>
                  {detail.embedding_strategy}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="top_k">{detail.top_k}</Descriptions.Item>
              <Descriptions.Item label="改写">{detail.rewrite}</Descriptions.Item>
              <Descriptions.Item label="可评测">{`${detail.n_evaluable}/${detail.n_queries}`}</Descriptions.Item>
              <Descriptions.Item label="耗时">{fmtMs(detail.duration_ms)}</Descriptions.Item>
              <Descriptions.Item label="加权质量">
                {detail.aggregate?.weighted_quality?.toFixed(3) ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label="时间">{fmtTime(detail.created_at)}</Descriptions.Item>
            </Descriptions>

            {/* 5 维均值 */}
            {detail.aggregate && (
              <Card size="small" title="5 维均值" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
                <Row gutter={16}>
                  {DIM_KEYS.map((dim) => (
                    <Col key={dim}>
                      <Statistic
                        title={DIM_META[dim]?.label ?? dim}
                        value={
                          detail.aggregate!.means[dim] == null
                            ? "—"
                            : detail.aggregate!.means[dim]!.toFixed(2)
                        }
                      />
                    </Col>
                  ))}
                </Row>
              </Card>
            )}

            {detail.error_message && (
              <Paragraph type="danger" style={{ fontSize: 12, marginBottom: 12 }}>
                {detail.error_message}
              </Paragraph>
            )}

            <Title level={5}>逐 query 评判明细 ({detail.per_query?.length ?? 0})</Title>
            <Table<QAPerQueryRow>
              rowKey="id"
              size="small"
              dataSource={detail.per_query ?? []}
              columns={perQueryColumns}
              pagination={{ pageSize: 10, showSizeChanger: false }}
              scroll={{ x: 1200 }}
              expandable={{
                expandedRowRender: (r) =>
                  r.rationale ? (
                    <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
                      <Text strong>评判理由：</Text> {r.rationale}
                    </Paragraph>
                  ) : null,
              }}
            />
          </>
        )}
      </Drawer>
    </>
  );
}
