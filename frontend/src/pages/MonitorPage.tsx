/**
 * 系统监控页（Phase 8.4）：检索性能 / API 用量 / 索引规模 / 资源健康。
 * 对齐后端 /v1/monitor/* 4 个只读端点（无新表/迁移/依赖，全聚合既有数据源）。
 * 骨架镜像 AgentsPage：local state + Promise.all + 20s 轮询；无图表库（漏斗用 Statistic）。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  getApiUsage,
  getIndexStats,
  getResources,
  getRetrievalPerf,
  getTrace,
  listTraces,
  type ApiUsage,
  type ComponentInfo,
  type IndexStats,
  type MilvusCollectionStat,
  type MonitorWindow,
  type Resources,
  type RetrievalPerf,
  type TraceDetail,
  type TraceListItem,
} from "../api/monitor";
import TraceView from "../components/monitor/TraceView";

const { Text } = Typography;

// ---- 格式化 ----

const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString());
const num1 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(1));
const ms = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v)} ms`);
const ratio = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(0)}%`);
const pct100 = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)}%`);

const fmtBytes = (b: number | null | undefined) => {
  if (b == null) return "—";
  if (b < 1024) return `${b} B`;
  const u = ["KB", "MB", "GB", "TB"];
  let v = b / 1024;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${u[i]}`;
};

// ---- 组件元数据（resources tab） ----

interface ComponentMeta {
  key: string;
  label: string;
  sizeLabel: string;
  size: (c: ComponentInfo | undefined) => number | null | undefined;
  sub?: (c: ComponentInfo) => string;
}

const COMPONENT_META: ComponentMeta[] = [
  { key: "postgres", label: "PostgreSQL", sizeLabel: "数据库大小", size: (c) => c?.db_size_bytes },
  { key: "redis", label: "Redis", sizeLabel: "已用内存", size: (c) => c?.used_memory_bytes, sub: (c) => `键 ${num(c.keys)}` },
  { key: "milvus", label: "Milvus", sizeLabel: "向量数", size: (c) => c?.rows, sub: (c) => `collection ${num(c.collections)}` },
  { key: "elasticsearch", label: "Elasticsearch", sizeLabel: "文档数", size: (c) => c?.doc_count, sub: (c) => `存储 ${fmtBytes(c.size_bytes)}` },
  { key: "minio", label: "MinIO", sizeLabel: "索引资产", size: (c) => c?.asset_bytes },
];

// ---- 索引统计表 ----

const pgRows = (s: IndexStats["postgres"]) => [
  { k: "代码 chunk", v: `${num(s.code_chunks)}（活跃 ${num(s.code_chunks_active)}）`, extra: `Milvus 同步 ${pct100(s.code_chunks_synced_pct)}` },
  { k: "文档 chunk", v: `${num(s.doc_chunks)}（活跃 ${num(s.doc_chunks_active)}）`, extra: `Milvus 同步 ${pct100(s.doc_chunks_synced_pct)}` },
  { k: "关联关系", v: `${num(s.chunk_relations)}`, extra: `过时 ${num(s.chunk_relations_stale)}` },
  { k: "调用图", v: `${num(s.call_graph)}`, extra: `活跃 ${num(s.call_graph_active)}` },
  { k: "文件", v: `代码 ${num(s.code_files)} · 文档 ${num(s.doc_files)}` },
  { k: "资源/日志", v: `doc_resources ${num(s.doc_resources)} · retrieval_logs ${num(s.retrieval_logs)}` },
  { k: "会话/消息", v: `会话 ${num(s.conversations)} · 消息 ${num(s.chat_messages)}` },
];

const milvusColumns: ColumnsType<MilvusCollectionStat> = [
  { title: "Collection", dataIndex: "name" },
  { title: "维度", dataIndex: "dim", width: 100, render: (d: number | null) => num(d) },
  { title: "向量数", dataIndex: "rows", width: 120, render: (n: number | null) => num(n) },
];

export default function MonitorPage() {
  const [perf, setPerf] = useState<RetrievalPerf | null>(null);
  const [usage, setUsage] = useState<ApiUsage | null>(null);
  const [indexStats, setIndexStats] = useState<IndexStats | null>(null);
  const [resources, setResources] = useState<Resources | null>(null);
  const [traces, setTraces] = useState<TraceListItem[] | null>(null);
  const [traceDetail, setTraceDetail] = useState<TraceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [timeWindow, setTimeWindow] = useState<MonitorWindow>("today");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [p, u, idx, res] = await Promise.all([
        getRetrievalPerf(timeWindow),
        getApiUsage(timeWindow),
        getIndexStats(),
        getResources(),
      ]);
      setPerf(p);
      setUsage(u);
      setIndexStats(idx);
      setResources(res);
      // M41: traces 独立容错，失败不阻塞既有四卡
      try {
        setTraces((await listTraces(timeWindow)).items);
      } catch { /* traces 降级，静默 */ }
    } catch (e) {
      message.error((e as Error).message || "加载监控数据失败");
    } finally {
      setLoading(false);
    }
  }, [timeWindow]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 20_000);
    return () => clearInterval(t);
  }, [refresh]);

  const openTrace = async (logId: number) => {
    setTraceDetail(null); // 防旧详情闪烁
    try {
      setTraceDetail(await getTrace(logId));
    } catch {
      message.error("加载链路详情失败");
    }
  };

  const healthy = resources?.status === "healthy";

  return (
    <div style={{ padding: 20, height: "100%", overflow: "auto" }}>
      <Card size="small" styles={{ body: { padding: "12px 20px" } }} style={{ marginBottom: 16 }}>
        <Row gutter={24} align="middle">
          <Col>
            <Statistic
              title="基础设施"
              valueRender={() =>
                resources ? (
                  <Tag color={healthy ? "success" : "error"} style={{ fontSize: 14 }}>
                    {healthy ? "● 正常" : "● 降级"}
                  </Tag>
                ) : (
                  <Text type="secondary">—</Text>
                )
              }
            />
          </Col>
          <Col>
            <Statistic title="代码 chunk" value={num(indexStats?.postgres.code_chunks)} />
          </Col>
          <Col>
            <Statistic title="文档 chunk" value={num(indexStats?.postgres.doc_chunks)} />
          </Col>
          <Col>
            <Statistic title="检索次数" value={num(indexStats?.postgres.retrieval_logs)} />
          </Col>
          <Col>
            <Statistic title="会话消息" value={num(indexStats?.postgres.chat_messages)} />
          </Col>
          <Col flex="auto" />
          <Col>
            <Space>
              <Select<MonitorWindow>
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

      <Card size="small">
        <Tabs
          defaultActiveKey="perf"
          items={[
            {
              key: "perf",
              label: "检索性能",
              children: (
                <Space direction="vertical" size={16} style={{ width: "100%" }}>
                  <Row gutter={24}>
                    <Col><Statistic title="检索总数" value={num(perf?.queries)} /></Col>
                    <Col><Statistic title="平均延迟" value={ms(perf?.latency_ms.avg_total)} /></Col>
                    <Col><Statistic title="P50 延迟" value={ms(perf?.latency_ms.p50_total)} /></Col>
                    <Col>
                      <Statistic
                        title="P95 延迟"
                        value={ms(perf?.latency_ms.p95_total)}
                        valueStyle={
                          perf?.latency_ms.p95_total && perf.latency_ms.p95_total > 2000
                            ? { color: "#fa8c16" }
                            : undefined
                        }
                      />
                    </Col>
                    <Col><Statistic title="召回阶段" value={ms(perf?.latency_ms.avg_recall)} /></Col>
                    <Col><Statistic title="重排阶段" value={ms(perf?.latency_ms.avg_rerank)} /></Col>
                  </Row>
                  <Card size="small" title="召回漏斗（legacy/retrieve 路径均值）" styles={{ body: { padding: 16 } }}>
                    <Row gutter={24} align="middle">
                      <Col><Statistic title="RRF 融合池" value={num1(perf?.funnel.avg_pool)} /></Col>
                      <Col><Text type="secondary">→</Text></Col>
                      <Col><Statistic title="精排候选" value={num1(perf?.funnel.avg_final)} /></Col>
                      <Col flex="auto" />
                      <Col><Statistic title="精排启用率" value={ratio(perf?.rerank_rate)} /></Col>
                      <Col>
                        <Statistic
                          title="反馈"
                          valueRender={() => (
                            <Text>
                              👍 {perf?.feedback.helpful ?? 0} · 👎 {perf?.feedback.not_helpful ?? 0}
                            </Text>
                          )}
                        />
                      </Col>
                    </Row>
                  </Card>
                </Space>
              ),
            },
            {
              key: "resources",
              label: "资源健康",
              children: (
                <Row gutter={[16, 16]}>
                  {COMPONENT_META.map((m) => {
                    const c = resources?.components?.[m.key];
                    const up = c?.up;
                    return (
                      <Col key={m.key} xs={24} sm={12} lg={8} xl={4}>
                        <Card size="small" title={m.label} styles={{ body: { padding: 16 } }}>
                          <Tag color={up ? "success" : "error"}>{up ? "正常" : "异常"}</Tag>
                          <div style={{ marginTop: 12 }}>
                            <Statistic title={m.sizeLabel} value={m.key === "minio" ? fmtBytes(m.size(c)) : num(m.size(c))} />
                          </div>
                          {m.sub && c ? (
                            <Text type="secondary" style={{ fontSize: 12 }}>{m.sub(c)}</Text>
                          ) : null}
                          {up === false && c?.detail ? (
                            <div>
                              <Text type="danger" style={{ fontSize: 11 }}>{c.detail}</Text>
                            </div>
                          ) : null}
                        </Card>
                      </Col>
                    );
                  })}
                </Row>
              ),
            },
            {
              key: "usage",
              label: "API 用量",
              children: (
                <Space direction="vertical" size={16} style={{ width: "100%" }}>
                  <Alert type="info" showIcon message={usage?.note} />
                  <Row gutter={24}>
                    <Col><Statistic title="LLM 调用" value={num(usage?.llm_calls)} /></Col>
                    <Col><Statistic title="查询嵌入调用" value={num(usage?.embedding_query_calls)} /></Col>
                    <Col><Statistic title="精排调用" value={num(usage?.rerank_calls)} /></Col>
                    <Col><Statistic title="生成 token（估）" value={num(usage?.generated_tokens_est)} /></Col>
                    <Col><Statistic title="已索引 token" value={num(usage?.indexed_tokens)} /></Col>
                  </Row>
                </Space>
              ),
            },
            {
              key: "index",
              label: "索引统计",
              children: (
                <Space direction="vertical" size={16} style={{ width: "100%" }}>
                  <Card size="small" title="PostgreSQL（PG 为真相源）">
                    <Table
                      rowKey="k"
                      size="small"
                      pagination={false}
                      loading={loading}
                      dataSource={indexStats ? pgRows(indexStats.postgres).map((r) => ({ ...r })) : []}
                      columns={[
                        { title: "对象", dataIndex: "k", width: 140 },
                        { title: "数量", dataIndex: "v" },
                        { title: "备注", dataIndex: "extra", render: (t: string | undefined) => (t ? <Text type="secondary">{t}</Text> : null) },
                      ]}
                    />
                  </Card>
                  <Card size="small" title={`Milvus（策略：${indexStats?.milvus.strategy ?? "—"}）`}>
                    <Table<MilvusCollectionStat>
                      rowKey="name"
                      size="small"
                      pagination={false}
                      loading={loading}
                      dataSource={indexStats?.milvus.collections ?? []}
                      columns={milvusColumns}
                    />
                  </Card>
                  <Card size="small" title="Elasticsearch">
                    <Row gutter={24}>
                      <Col><Statistic title="索引" value={indexStats?.elasticsearch.index ?? "—"} /></Col>
                      <Col><Statistic title="文档总数" value={num(indexStats?.elasticsearch.doc_count)} /></Col>
                      <Col><Statistic title="code 文档" value={num(indexStats?.elasticsearch.by_kind?.code)} /></Col>
                      <Col><Statistic title="doc 文档" value={num(indexStats?.elasticsearch.by_kind?.doc)} /></Col>
                    </Row>
                  </Card>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card size="small" title="全链路追溯（M41）" styles={{ body: { padding: 16 } }}>
        <div style={{ display: "flex", gap: 16 }}>
          <Table
            size="small"
            rowKey="log_id"
            dataSource={traces ?? []}
            onRow={(r) => ({ onClick: () => void openTrace(r.log_id), style: { cursor: "pointer" } })}
            pagination={{ pageSize: 8 }}
            columns={[
              { title: "log_id", dataIndex: "log_id", width: 80 },
              { title: "查询", dataIndex: "query", ellipsis: true },
              { title: "模式", dataIndex: "mode", width: 90,
                render: (v: string | null, r: TraceListItem) => v ?? "—"
              },
              { title: "耗时", dataIndex: "total_ms", width: 90, render: (v) => ms(v) },
              { title: "token", width: 110,
                render: (_: unknown, r: TraceListItem) =>
                  r.tokens ? `${r.tokens.prompt}+${r.tokens.completion}${r.tokens.estimated ? "*" : ""}` : "—"
              },
              { title: "trace", dataIndex: "has_trace", width: 70,
                render: (v: boolean) => (v ? <Tag color="green">v2</Tag> : <Tag>旧</Tag>)
              },
            ]}
            style={{ width: "55%" }}
          />
          <div style={{ flex: 1, minWidth: 0 }}>
            <TraceView detail={traceDetail} />
          </div>
        </div>
      </Card>
    </div>
  );
}
