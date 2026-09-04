import { useEffect, useMemo, useState } from "react";
import {
  Alert, Button, Card, Checkbox, Drawer, Input, Space, Spin, Switch, Table, Typography, message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  getEvalRun, listEvalRuns, runEval,
  type EvalQueryRow, type EvalRunDetail, type EvalRunSummary, type VariantMetrics,
} from "../api/eval";

const { Text, Title } = Typography;

/** 指标行定义：key 对齐 metrics.aggregate 冻结键；fmt=null 显示 "—"。 */
const METRIC_DEFS: { key: keyof VariantMetrics; label: string; pct?: boolean; digits?: number }[] = [
  { key: "code_hit_rate", label: "代码定位命中率", pct: true, digits: 1 },
  { key: "doc_hit_rate", label: "文档命中率", pct: true, digits: 1 },
  { key: "citation_precision", label: "引用准确率", pct: true, digits: 1 },
  { key: "rounds_mean", label: "平均轮次", digits: 1 },
  { key: "rounds_p95", label: "轮次 P95", digits: 1 },
  { key: "latency_p50_ms", label: "延迟 P50 (ms)", digits: 0 },
  { key: "latency_p95_ms", label: "延迟 P95 (ms)", digits: 0 },
  { key: "tokens_mean", label: "均 Token", digits: 0 },
  { key: "n_cases", label: "case 数", digits: 0 },
];

const fmtMetric = (v: number | null | undefined, pct?: boolean, digits = 1): string => {
  if (v === null || v === undefined) return "—";
  return pct ? `${(v * 100).toFixed(digits)}%` : v.toFixed(digits);
};

const JUDGE_LABELS: Record<string, string> = {
  faithfulness: "忠实度",
  answer_relevance: "切题度",
  citation_accuracy: "引用一致性",
  hallucination: "幻觉（低=好）",
};

export default function EvalPage() {
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [detail, setDetail] = useState<EvalRunDetail | null>(null);
  const [drawerRows, setDrawerRows] = useState<EvalQueryRow[] | null>(null);
  const [drawerCase, setDrawerCase] = useState<string>("");
  const [repo, setRepo] = useState("");
  const [judge, setJudge] = useState(false);
  const [abRounds4, setAbRounds4] = useState(false);
  const [abNoGraph, setAbNoGraph] = useState(false);
  const [abModel, setAbModel] = useState("");
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      setRuns((await listEvalRuns()).items);
    } catch {
      /* 历史加载失败不阻塞页面 */
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void refresh(); }, []);

  const onRun = async () => {
    const variants = [];
    if (abRounds4) variants.push({ name: "rounds4", rounds_code: 4, rounds_doc: 2 });
    if (abNoGraph) variants.push({ name: "nograph", code_no_graph: true });
    if (abModel.trim()) variants.push({ name: `model-${abModel.trim()}`, model_reasoning: abModel.trim() });
    setRunning(true);
    try {
      const result = await runEval({ repo: repo.trim() || undefined, judge, variants });
      setDetail(result);
      void refresh();
      message.success(`评测完成：${result.status}`);
    } catch (e) {
      message.error(`评测失败：${(e as Error).message}`);
    } finally {
      setRunning(false);
    }
  };

  const openDetail = async (id: number) => setDetail(await getEvalRun(id));

  const variantNames = useMemo(
    () => Object.keys(detail?.metrics?.variants ?? {}), [detail]);

  const variantColumns: ColumnsType<Record<string, unknown>> = useMemo(() => [
    { title: "指标", dataIndex: "label", key: "label" },
    ...variantNames.map((name) => ({
      title: name, dataIndex: name, key: name,
    })),
  ], [variantNames]);

  const variantDataSource = useMemo(() => {
    if (!detail?.metrics) return [];
    return METRIC_DEFS.map((d) => {
      const row: Record<string, unknown> = { key: d.key, label: d.label };
      for (const name of variantNames) {
        const v = detail.metrics?.variants[name]?.[d.key];
        row[name] = v === null || v === undefined ? "—" : fmtMetric(v as number, d.pct, d.digits);
      }
      return row;
    });
  }, [detail, variantNames]);

  const historyColumns: ColumnsType<EvalRunSummary> = [
    { title: "ID", dataIndex: "id", key: "id", width: 70 },
    { title: "仓库", dataIndex: "repo", key: "repo", width: 110 },
    { title: "类型", dataIndex: "kind", key: "kind", width: 80,
      render: (k) => (k === "ab" ? "A/B" : "单次") },
    { title: "状态", dataIndex: "status", key: "status", width: 90,
      render: (s) => (s === "DONE" ? <Text type="success">DONE</Text>
        : s === "FAILED" ? <Text type="danger">FAILED</Text> : <Text type="warning">RUNNING</Text>) },
    { title: "命中率", key: "hit", width: 100,
      render: (_, r) => {
        const first = Object.values(r.metrics?.variants ?? {})[0];
        return first?.code_hit_rate === null || first?.code_hit_rate === undefined
          ? "—" : fmtMetric(first.code_hit_rate, true);
      } },
    { title: "创建时间", dataIndex: "created_at", key: "created_at",
      render: (v: string | null) => (v ? new Date(v).toLocaleString() : "—") },
    { title: "操作", key: "op", width: 90,
      render: (_, r) => <a onClick={() => void openDetail(r.id)}>详情</a> },
  ];

  const caseIds = useMemo(() => {
    const ids: string[] = [];
    for (const row of detail?.per_query ?? []) {
      if (!ids.includes(row.case_id)) ids.push(row.case_id);
    }
    return ids;
  }, [detail]);

  const drawerColumns: ColumnsType<EvalQueryRow> = [
    { title: "变体", dataIndex: "variant", key: "variant", width: 100 },
    { title: "代码命中", dataIndex: "hit_code", key: "hit_code", width: 90,
      render: (v: boolean, r) => (r.has_code_anchor === false && r.has_doc_anchor === false
        ? "—" : v ? "✓" : "✗") },
    { title: "匹配/引用", key: "m", width: 100,
      render: (_, r) => `${r.matched}/${r.total}` },
    { title: "轮次", dataIndex: "rounds", key: "rounds", width: 70 },
    { title: "延迟(ms)", dataIndex: "latency_ms", key: "latency_ms", width: 90 },
    { title: "路由", dataIndex: "route", key: "route", width: 100 },
    { title: "unresolved", dataIndex: "unresolved", key: "unresolved",
      render: (v: string[]) => (v.length ? <Text type="warning">{v.join(", ")}</Text> : "—") },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card size="small" title="运行评测">
        <Space wrap size={12}>
          <Input placeholder="repo（默认 golden 顶层）" style={{ width: 200 }} value={repo}
                 onChange={(e) => setRepo(e.target.value)} />
          <span>LLM 评判 <Switch checked={judge} onChange={setJudge} /></span>
          <Checkbox checked={abRounds4} onChange={(e) => setAbRounds4(e.target.checked)}>
            A/B：轮数减半
          </Checkbox>
          <Checkbox checked={abNoGraph} onChange={(e) => setAbNoGraph(e.target.checked)}>
            A/B：禁图工具
          </Checkbox>
          <Input placeholder="A/B：reasoning 模型名" style={{ width: 200 }} value={abModel}
                 onChange={(e) => setAbModel(e.target.value)} />
          <Button type="primary" loading={running} onClick={() => void onRun()}>
            运行
          </Button>
        </Space>
      </Card>

      {detail && (
        <Card size="small" title={`最近一次：run #${detail.id}（${detail.repo} · ${detail.kind}）`}>
          {detail.status === "FAILED" && (
            <Alert type="error" showIcon message={`评测失败：${detail.error ?? ""}`} style={{ marginBottom: 12 }} />
          )}
          <Table size="small" pagination={false} columns={variantColumns}
                 dataSource={variantDataSource} />
          {detail.metrics?.judge && (
            <Space size={24} style={{ marginTop: 12 }}>
              {Object.entries(detail.metrics.judge).map(([k, v]) => (
                <Text key={k}>{JUDGE_LABELS[k] ?? k}：<b>{(v as number).toFixed(2)}</b></Text>
              ))}
            </Space>
          )}
          {detail.per_query && detail.per_query.length > 0 && (
            <>
              <Title level={5} style={{ marginTop: 16 }}>逐 case 明细</Title>
              <Space wrap>
                {caseIds.map((cid) => (
                  <Button key={cid} size="small"
                          onClick={() => {
                            setDrawerCase(cid);
                            setDrawerRows(
                              (detail.per_query ?? []).filter((r) => r.case_id === cid));
                          }}>
                    {cid}
                  </Button>
                ))}
              </Space>
            </>
          )}
        </Card>
      )}

      <Card size="small" title="历史运行">
        <Spin spinning={loading}>
          <Table size="small" rowKey="id" pagination={{ pageSize: 10 }}
                 columns={historyColumns} dataSource={runs} />
        </Spin>
      </Card>

      <Drawer title={`case ${drawerCase}`} open={drawerRows !== null} width={720}
              onClose={() => setDrawerRows(null)}>
        <Table size="small" rowKey="variant" pagination={false}
               columns={drawerColumns} dataSource={drawerRows ?? []} />
      </Drawer>
    </Space>
  );
}
