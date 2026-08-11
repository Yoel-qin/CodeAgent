/**
 * 检索评测页（Phase 9 评测产品化 M27 / A/B 消融产品化 M28）：
 *  - 单次评测 Tab（M27）：运行评测 + KPI + 历史表 + 趋势 sparkline + 详情抽屉。
 *  - A/B 消融 Tab（M28）：选 pair + 运行 + 各变体 KPI + pair delta + A/B 历史/详情抽屉。
 * 对齐后端 /v1/eval/run（POST）+ /v1/eval/runs（GET 列表）+ /v1/eval/runs/{id}（GET 详情）
 *  + /v1/eval/ab（POST）+ /v1/eval/ab-runs（GET 列表）+ /v1/eval/ab-runs/{id}（GET 详情）。
 * 骨架镜像 MonitorPage（local state + 挂载载入 + Tabs）+ SyncPage（POST + 历史 + Drawer）。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  Descriptions,
  Drawer,
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
import { PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import Sparkline from "../components/Sparkline";
import {
  getAbRun,
  getEvalRun,
  listAbRuns,
  listEvalRuns,
  runAb,
  runEval,
  type ABPairResult,
  type ABRunDetail,
  type ABRunSummary,
  type ABVariantResult,
  type EvalRunDetail,
  type EvalRunSummary,
  type PerQueryRow,
} from "../api/eval";

const { Text, Paragraph, Title } = Typography;

// ---- 格式化 ----
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

const at = (agg: { recall?: Record<string, number | null> } | null | undefined, k: string) =>
  agg?.recall?.[k] ?? null;

const TOPK_OPTIONS = [8, 10, 15, 20].map((k) => ({ value: k, label: `top_k=${k}` }));
const REWRITE_OPTIONS = [
  { value: "off", label: "改写: off" },
  { value: "auto", label: "改写: auto" },
];
// 可选 A/B pair（对齐后端 ab_service.DEFAULT_PAIRS）
const PAIR_OPTIONS = [
  { value: "rerank", label: "rerank（精排 vs 无精排）" },
  { value: "multipath_rrf", label: "multipath_rrf（多路 vs 仅向量）" },
  { value: "graph", label: "graph（图遍历 on/off）" },
];
// 单次评测可关掉的环节（对齐后端 AblationConfig 4 字段）；选中=关掉该环节
const ABLATION_OPTIONS = [
  { value: "vector", label: "关向量" },
  { value: "lexical", label: "关词法" },
  { value: "graph", label: "关图遍历" },
  { value: "rerank", label: "关精排" },
];

const historyColumns = (onDetail: (id: number) => void): ColumnsType<EvalRunSummary> => [
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
    width: 100,
    render: (s: string) => <Tag color={STRATEGY_COLOR[s] || "default"}>{s}</Tag>,
  },
  { title: "top_k", dataIndex: "top_k", width: 70 },
  { title: "改写", dataIndex: "rewrite", width: 70 },
  {
    title: "消融",
    width: 110,
    render: (_, r) => {
      const off = Object.keys(r.ablation || {}).filter((k) => r.ablation?.[k] === false);
      return off.length ? (
        <Space size={2} wrap>
          {off.map((k) => (
            <Tag key={k} color="orange" style={{ margin: 1 }}>
              关{k}
            </Tag>
          ))}
        </Space>
      ) : (
        <Text type="secondary">—</Text>
      );
    },
  },
  {
    title: "可评测",
    width: 100,
    render: (_, r) => `${num(r.n_evaluable)}/${num(r.n_queries)}`,
  },
  { title: "重排命中", dataIndex: "rerank_on_count", width: 90 },
  { title: "耗时", dataIndex: "duration_ms", width: 90, render: fmtMs },
  {
    title: "Recall@10",
    width: 100,
    render: (_, r) => ratio(at(r.aggregate, "10")),
  },
  {
    title: "MRR",
    width: 80,
    render: (_, r) => (r.aggregate?.mrr == null ? "—" : r.aggregate.mrr.toFixed(3)),
  },
  {
    title: "幽灵",
    dataIndex: "unresolved_count",
    width: 70,
    render: (n: number) =>
      n > 0 ? <Text type="danger">{n}</Text> : <Text type="secondary">0</Text>,
  },
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

const perQueryColumns: ColumnsType<PerQueryRow> = [
  { title: "ID", dataIndex: "id", width: 70 },
  { title: "查询", dataIndex: "text", ellipsis: true },
  {
    title: "Recall@10",
    width: 100,
    render: (_, r) => ratio(r.recall?.["10"] ?? null),
  },
  {
    title: "MRR",
    width: 80,
    render: (_, r) => (r.mrr == null ? "—" : r.mrr.toFixed(3)),
  },
  {
    title: "首中位",
    dataIndex: "first_hit_rank",
    width: 80,
    render: (n: number | null | undefined) => (n == null ? "—" : `#${n}`),
  },
  {
    title: "精排",
    dataIndex: "rerank_on",
    width: 70,
    render: (on: boolean) => (on ? <Tag color="green">on</Tag> : <Tag>off</Tag>),
  },
  {
    title: "错误",
    dataIndex: "error",
    width: 120,
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

// =================== A/B 消融 Tab（M28）===================

const abHistoryColumns = (onDetail: (id: number) => void): ColumnsType<ABRunSummary> => [
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
  {
    title: "对照组",
    width: 180,
    render: (_, r) => r.pairs.map((p) => <Tag key={p.name}>{p.name}</Tag>),
  },
  {
    title: "Recall@10 (full)",
    width: 130,
    render: (_, r) => ratio(at(r.aggregate, "10")),
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

const variantColumns: ColumnsType<[string, ABVariantResult]> = [
  { title: "变体", width: 120, render: (_, [name]) => <Tag color="blue">{name}</Tag> },
  { title: "说明", dataIndex: 1, render: (_, [, v]) => v.desc, ellipsis: true },
  {
    title: "精排",
    width: 70,
    render: (_, [, v]) => (v.ablation.rerank ? <Tag color="green">on</Tag> : <Tag>off</Tag>),
  },
  { title: "可评测", width: 90, render: (_, [, v]) => `${v.n_evaluable}/${v.n_queries}` },
  {
    title: "Recall@10",
    width: 100,
    render: (_, [, v]) => ratio(at(v.aggregate, "10")),
  },
  {
    title: "MRR",
    width: 80,
    render: (_, [, v]) => (v.aggregate?.mrr == null ? "—" : v.aggregate.mrr.toFixed(3)),
  },
  {
    title: "NDCG@10",
    width: 100,
    render: (_, [, v]) => ratio(v.aggregate?.ndcg?.["10"] ?? null),
  },
];

// 取 pair.delta[metric] 的 delta（mrr 单值 ABDelta；recall/precision/ndcg 按 K 的 map）。
// 联合类型 Record<string,ABDelta> | ABDelta 用 "abs" in d 判别后仍需 cast，故统一走显式解构。
function pairMetricDelta(
  pair: ABPairResult,
  metric: string,
  k: string,
): { abs: number | null; pct: number | null } {
  const d = pair.delta[metric];
  if (!d) return { abs: null, pct: null };
  type Cell = { abs: number | null; pct: number | null };
  // ABDelta（mrr 单值）的 abs 是 number|null；按 K 的 map 顶层无 abs 键（值为 Cell）。
  // 联合类型下用 typeof 判别 abs 字段是否为 number，收窄 mrr 单值分支。
  if (typeof (d as Cell).abs === "number") {
    const c = d as Cell;
    return { abs: c.abs, pct: c.pct };
  }
  const cell = (d as Record<string, Cell>)[k];
  return { abs: cell?.abs ?? null, pct: cell?.pct ?? null };
}

function PairDeltaCard({ pair }: { pair: ABPairResult }) {
  const ks = ["1", "3", "5", "10"];
  return (
    <Card
      size="small"
      title={
        <Space>
          <Tag color="geekblue">{pair.name}</Tag>
          <Text type="secondary">{pair.claim}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {pair.baseline} → {pair.treatment}
          </Text>
        </Space>
      }
      style={{ marginBottom: 12 }}
    >
      <Table
        rowKey={(r) => r}
        size="small"
        pagination={false}
        dataSource={pair.metric_focus}
        columns={[
          { title: "指标", width: 110, render: (m: string) => m },
          {
            title: "K",
            width: 60,
            render: (m: string) => (m === "mrr" ? "—" : ks.join("/")),
          },
          {
            title: "Δabs / Δpct",
            render: (m: string) =>
              m === "mrr" ? (
                <DeltaCell {...pairMetricDelta(pair, "mrr", "")} />
              ) : (
                <Space wrap>
                  {ks.map((k) => (
                    <Tag key={k} style={{ margin: 2 }}>
                      @{k} <DeltaCell inline {...pairMetricDelta(pair, m, k)} />
                    </Tag>
                  ))}
                </Space>
              ),
          },
        ]}
      />
    </Card>
  );
}

function DeltaCell({
  abs,
  pct,
  inline,
}: {
  abs: number | null;
  pct: number | null;
  inline?: boolean;
}) {
  if (abs == null && pct == null) return <Text type="secondary">—</Text>;
  const positive = (pct ?? 0) >= 0;
  return (
    <Text type={positive ? "success" : "danger"} style={{ fontSize: inline ? 12 : 13 }}>
      {abs != null ? `${abs >= 0 ? "+" : ""}${abs.toFixed(3)}` : "—"}
      {pct != null ? ` (${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%)` : ""}
    </Text>
  );
}

// ---- A/B 逐 query 配对明细（M29）----

interface PairedQueryRow {
  id: string;
  text: string;
  bRank: number | null | undefined; // baseline first_hit_rank
  tRank: number | null | undefined; // treatment first_hit_rank
}

/** 按 query id join 一对 pair 的 baseline/treatment per_query，取 first_hit_rank 对照。 */
function pairPerQuery(
  detail: ABRunDetail,
  pair: ABPairResult,
): PairedQueryRow[] {
  const bRows = detail.variants?.[pair.baseline]?.per_query ?? [];
  const tRows = detail.variants?.[pair.treatment]?.per_query ?? [];
  const tMap = new Map<string, PerQueryRow>();
  for (const r of tRows) tMap.set(String(r.id), r);
  return bRows.map((b) => {
    const t = tMap.get(String(b.id));
    return {
      id: String(b.id ?? ""),
      text: String(b.text ?? ""),
      bRank: b.first_hit_rank,
      tRank: t?.first_hit_rank,
    };
  });
}

const rankCell = (n: number | null | undefined) =>
  n == null ? <Text type="secondary">未中</Text> : `#${n}`;

function PairPerQueryTable({ detail, pair }: { detail: ABRunDetail; pair: ABPairResult }) {
  const rows = pairPerQuery(detail, pair);
  const cols: ColumnsType<PairedQueryRow> = [
    { title: "ID", dataIndex: "id", width: 70 },
    { title: "查询", dataIndex: "text", ellipsis: true },
    {
      title: `${pair.baseline} 首中位`,
      width: 120,
      render: (_, r) => rankCell(r.bRank),
    },
    {
      title: `${pair.treatment} 首中位`,
      width: 120,
      render: (_, r) => rankCell(r.tRank),
    },
    {
      title: "Δrank",
      width: 100,
      render: (_, r) => {
        if (r.bRank == null || r.tRank == null) return <Text type="secondary">—</Text>;
        const d = r.bRank - r.tRank; // 正=被拉上来（treatment 排名更靠前）
        const pos = d >= 0;
        return (
          <Text type={pos ? "success" : "danger"} style={{ fontSize: 12 }}>
            {pos ? "↑" : "↓"} {Math.abs(d)}
          </Text>
        );
      },
    },
  ];
  return (
    <Table<PairedQueryRow>
      rowKey="id"
      size="small"
      dataSource={rows}
      columns={cols}
      pagination={{ pageSize: 8, showSizeChanger: false }}
      scroll={{ x: 560 }}
    />
  );
}

function AbEvalTab() {
  const [history, setHistory] = useState<ABRunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [pairs, setPairs] = useState<string[]>(["rerank", "multipath_rrf", "graph"]);
  const [topK, setTopK] = useState(10);
  const [rewrite, setRewrite] = useState<"off" | "auto">("off");
  const [graphSubset, setGraphSubset] = useState(false);
  const [diagnose, setDiagnose] = useState(false);
  const [detail, setDetail] = useState<ABRunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listAbRuns(50);
      setHistory(data.items);
    } catch (e) {
      message.error((e as Error).message || "加载 A/B 历史失败");
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
      setDetail(await getAbRun(id, diagnose));
    } catch (e) {
      message.error((e as Error).message || "加载 A/B 详情失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const onRun = async () => {
    if (pairs.length === 0) {
      message.warning("请至少选择一组对照");
      return;
    }
    setTriggering(true);
    try {
      const res = await runAb({ pairs, top_k: topK, rewrite, graph_subset: graphSubset, diagnose });
      if (res.status === "COMPLETED") {
        const r10 = ratio(at(res.aggregate, "10"));
        message.success(`A/B 完成（full）：Recall@10 ${r10} · ${res.pairs.length} 组对照`);
      } else {
        message.warning(`A/B 未完成：${res.error_message || res.status}`);
      }
      await refresh();
    } catch (e) {
      message.error((e as Error).message || "运行 A/B 失败");
    } finally {
      setTriggering(false);
    }
  };

  // A/B 趋势：每行的 aggregate = full 变体锚点（M28 已冗余）；历史最新在前 → reverse 旧→新
  const trendRecall = history.map((h) => at(h.aggregate, "10")).reverse();
  const trendMrr = history.map((h) => h.aggregate?.mrr ?? null).reverse();
  const trendNdcg = history.map((h) => h.aggregate?.ndcg?.["10"] ?? null).reverse();

  return (
    <>
      <Card size="small" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col>
            <Text type="secondary">对照组：</Text>
            <Select
              mode="multiple"
              value={pairs}
              onChange={setPairs}
              style={{ minWidth: 360 }}
              options={PAIR_OPTIONS}
              maxTagCount="responsive"
            />
          </Col>
          <Col>
            <Select value={topK} onChange={setTopK} style={{ width: 90 }} options={TOPK_OPTIONS} />
          </Col>
          <Col>
            <Select
              value={rewrite}
              onChange={setRewrite}
              style={{ width: 120 }}
              options={REWRITE_OPTIONS}
            />
          </Col>
          <Col>
            <Checkbox checked={graphSubset} onChange={(e) => setGraphSubset(e.target.checked)}>
              graph 子集
            </Checkbox>
            <Checkbox checked={diagnose} onChange={(e) => setDiagnose(e.target.checked)}>
              向量路诊断
            </Checkbox>
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
                运行 A/B
              </Button>
            </Space>
          </Col>
        </Row>
        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          对照组经 AblationConfig 关闭某检索环节跑真实全漏斗，对照 on/off 的指标 delta（基线→全开）。
          3~4 变体 × ~85 query × 重排 API ≈ 数十秒~分钟级。
        </Paragraph>
      </Card>

      <Card
        size="small"
        title="full 变体质量趋势（历史，旧 → 新）"
        styles={{ body: { padding: 16 } }}
        style={{ marginBottom: 16 }}
      >
        <Row gutter={48}>
          <Col>
            <Statistic title="Recall@10 趋势" valueRender={() => <Sparkline values={trendRecall} color="#1677ff" />} />
          </Col>
          <Col>
            <Statistic title="MRR 趋势" valueRender={() => <Sparkline values={trendMrr} color="#52c41a" />} />
          </Col>
          <Col>
            <Statistic title="NDCG@10 趋势" valueRender={() => <Sparkline values={trendNdcg} color="#722ed1" />} />
          </Col>
        </Row>
      </Card>

      <Card size="small" title={`A/B 历史 (${history.length})`} style={{ marginBottom: 16 }}>
        <Table<ABRunSummary>
          rowKey="run_id"
          size="small"
          loading={loading}
          dataSource={history}
          columns={abHistoryColumns(openDetail)}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 980 }}
        />
      </Card>

      <Drawer
        title={`A/B #${detail?.run_id ?? ""} 详情`}
        open={!!detail}
        onClose={() => setDetail(null)}
        width={900}
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
            </Descriptions>

            {detail.error_message && (
              <Paragraph type="danger" style={{ fontSize: 12, marginBottom: 12 }}>
                {detail.error_message}
              </Paragraph>
            )}

            <Title level={5}>各变体指标</Title>
            <Table<[string, ABVariantResult]>
              rowKey={([name]) => name}
              size="small"
              pagination={false}
              dataSource={Object.entries(detail.variants)}
              columns={variantColumns}
              style={{ marginBottom: 16 }}
            />

            <Title level={5}>对照 delta（基线 → 全开）</Title>
            {detail.pairs.map((p) => (
              <PairDeltaCard key={p.name} pair={p} />
            ))}

            <Collapse
              size="small"
              style={{ marginTop: 16 }}
              items={detail.pairs.map((p) => ({
                key: p.name,
                label: (
                  <Space>
                    <Tag color="geekblue">{p.name}</Tag>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      逐 query 配对明细（{p.baseline} vs {p.treatment} 首中位）
                    </Text>
                  </Space>
                ),
                children: <PairPerQueryTable detail={detail} pair={p} />,
              }))}
            />
          </>
        )}
      </Drawer>
    </>
  );
}

// =================== 单次评测 Tab（M27，保持原行为）===================

export default function EvalPage() {
  const [history, setHistory] = useState<EvalRunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [topK, setTopK] = useState(10);
  const [rewrite, setRewrite] = useState<"off" | "auto">("off");
  const [ablation, setAblation] = useState<string[]>([]); // M29: 关掉的环节（空=全开=生产）
  const [detail, setDetail] = useState<EvalRunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listEvalRuns(50);
      setHistory(data.items);
    } catch (e) {
      message.error((e as Error).message || "加载评测历史失败");
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
      setDetail(await getEvalRun(id));
    } catch (e) {
      message.error((e as Error).message || "加载评测详情失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const onRun = async () => {
    setTriggering(true);
    try {
      // ablation: 选中=关掉该环节 → {key: false}；空对象省略（=全开=生产）
      const ablObj = ablation.length
        ? Object.fromEntries(ablation.map((k) => [k, false]))
        : undefined;
      const res = await runEval({ top_k: topK, rewrite, ablation: ablObj });
      if (res.status === "COMPLETED") {
        message.success(`评测完成：Recall@10 ${ratio(at(res.aggregate, "10"))} · MRR ${res.aggregate?.mrr?.toFixed(3) ?? "—"}`);
      } else {
        message.warning(`评测未完成：${res.error_message || res.status}`);
      }
      await refresh();
    } catch (e) {
      message.error((e as Error).message || "运行评测失败");
    } finally {
      setTriggering(false);
    }
  };

  const latest = history[0]?.aggregate ?? null;
  // 历史最新在前；趋势按时间正序（旧→新）画
  const trendRecall = history.map((h) => at(h.aggregate, "10")).reverse();
  const trendMrr = history.map((h) => h.aggregate?.mrr ?? null).reverse();
  const trendNdcg = history.map((h) => h.aggregate?.ndcg?.["10"] ?? null).reverse();

  return (
    <div style={{ padding: 20, height: "100%", overflow: "auto" }}>
      <Tabs
        defaultActiveKey="single"
        items={[
          {
            key: "single",
            label: "单次评测",
            children: (
              <>
      <Card size="small" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
        <Row gutter={24} align="middle">
          <Col>
            <Statistic title="Recall@10" value={ratio(at(latest, "10"))} />
          </Col>
          <Col>
            <Statistic title="MRR" value={latest?.mrr == null ? "—" : latest.mrr.toFixed(3)} />
          </Col>
          <Col>
            <Statistic title="NDCG@10" value={ratio(latest?.ndcg?.["10"] ?? null)} />
          </Col>
          <Col>
            <Statistic title="可评测 query" value={history[0] ? `${history[0].n_evaluable}/${history[0].n_queries}` : "—"} />
          </Col>
          <Col flex="auto" />
          <Col>
            <Space>
              <Select
                value={topK}
                onChange={setTopK}
                style={{ width: 90 }}
                options={TOPK_OPTIONS}
              />
              <Select
                value={rewrite}
                onChange={setRewrite}
                style={{ width: 120 }}
                options={REWRITE_OPTIONS}
              />
              <Select
                mode="multiple"
                value={ablation}
                onChange={setAblation}
                style={{ minWidth: 200 }}
                options={ABLATION_OPTIONS}
                maxTagCount="responsive"
                placeholder="消融：选关掉的环节"
              />
              <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>
                刷新
              </Button>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={onRun}
                loading={triggering}
              >
                运行评测
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Card size="small" title="质量趋势（历史，旧 → 新）" styles={{ body: { padding: 16 } }} style={{ marginBottom: 16 }}>
        <Row gutter={48}>
          <Col>
            <Statistic title="Recall@10 趋势" valueRender={() => <Sparkline values={trendRecall} color="#1677ff" />} />
          </Col>
          <Col>
            <Statistic title="MRR 趋势" valueRender={() => <Sparkline values={trendMrr} color="#52c41a" />} />
          </Col>
          <Col>
            <Statistic title="NDCG@10 趋势" valueRender={() => <Sparkline values={trendNdcg} color="#722ed1" />} />
          </Col>
        </Row>
      </Card>

      <Card size="small" title={`评测历史 (${history.length})`}>
        <Table<EvalRunSummary>
          rowKey="run_id"
          size="small"
          loading={loading}
          dataSource={history}
          columns={historyColumns(openDetail)}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 1180 }}
        />
      </Card>

      <Drawer
        title={`评测 #${detail?.run_id ?? ""} 详情`}
        open={!!detail}
        onClose={() => setDetail(null)}
        width={760}
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
                <Tag color={STRATEGY_COLOR[detail.embedding_strategy] || "default"}>{detail.embedding_strategy}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="top_k">{detail.top_k}</Descriptions.Item>
              <Descriptions.Item label="改写">{detail.rewrite}</Descriptions.Item>
              <Descriptions.Item label="可评测">{`${detail.n_evaluable}/${detail.n_queries}`}</Descriptions.Item>
              <Descriptions.Item label="重排命中">{detail.rerank_on_count}</Descriptions.Item>
              <Descriptions.Item label="耗时">{fmtMs(detail.duration_ms)}</Descriptions.Item>
              <Descriptions.Item label="幽灵">{detail.unresolved_count}</Descriptions.Item>
              <Descriptions.Item label="Recall@10">{ratio(at(detail.aggregate, "10"))}</Descriptions.Item>
              <Descriptions.Item label="MRR">{detail.aggregate?.mrr?.toFixed(3) ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="NDCG@10">{ratio(detail.aggregate?.ndcg?.["10"] ?? null)}</Descriptions.Item>
              <Descriptions.Item label="时间" span={2}>{fmtTime(detail.created_at)}</Descriptions.Item>
            </Descriptions>

            {detail.error_message && (
              <Paragraph type="danger" style={{ fontSize: 12, marginBottom: 12 }}>
                {detail.error_message}
              </Paragraph>
            )}

            <Typography.Title level={5}>
              逐 query 明细 ({detail.per_query?.length ?? 0})
            </Typography.Title>
            <Table<PerQueryRow>
              rowKey="id"
              size="small"
              dataSource={detail.per_query ?? []}
              columns={perQueryColumns}
              pagination={{ pageSize: 10, showSizeChanger: false }}
              scroll={{ x: 620 }}
            />

            {detail.unresolved && detail.unresolved.length > 0 && (
              <>
                <Typography.Title level={5} style={{ marginTop: 16 }}>
                  未解析 query ({detail.unresolved.length})
                </Typography.Title>
                {detail.unresolved.map((u, i) => (
                  <Paragraph key={i} type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
                    {String(u.id ?? "")}：{String(u.text ?? "")}（缺失标注 {JSON.stringify(u.missing ?? [])}）
                  </Paragraph>
                ))}
              </>
            )}
          </>
        )}
      </Drawer>
              </>
            ),
          },
          {
            key: "ab",
            label: "A/B 消融",
            children: <AbEvalTab />,
          },
        ]}
      />
    </div>
  );
}
