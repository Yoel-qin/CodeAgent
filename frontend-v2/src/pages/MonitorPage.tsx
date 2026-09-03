/**
 * 系统监控页（M7 v2）：概览 / 管道状态 / 全链路追溯三卡。
 * 对齐后端 Task 10 三端点（/v1/monitor/overview|traces[/{message_id}]|pipeline）——
 * 每段可 null（组件级软失败），null 段渲染降级态（「—」/「Redis 不可用」），不崩。
 * 三卡各自独立 loading/error（一卡失败显「加载失败」，不互相拖垮）；
 * window 切换重拉 overview + traces（pipeline 不随 window）。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Card,
  Col,
  Row,
  Segmented,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  getOverview,
  getPipelineStats,
  getTrace,
  listTraces,
  type MonitorWindow,
  type Overview,
  type PipelineStats,
  type TraceDetail,
  type TraceListItem,
  type TraceList,
} from "../api/monitor";
import TraceView from "../components/monitor/TraceView";

const { Text } = Typography;

// ---- 格式化（null → 「—」降级） ----

const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString());
const num1 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(1));
const ms = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v)} ms`);
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(0)}%`);
const fmtTime = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";

/** 失败卡统一降级文案。 */
const LoadFailed = () => <Text type="danger">加载失败</Text>;

export default function MonitorPage() {
  const [win, setWin] = useState<MonitorWindow>("7d");

  // 每卡独立 loading/error（软失败不互相拖垮）
  const [overview, setOverview] = useState<Overview | null>(null);
  const [ovLoading, setOvLoading] = useState(false);
  const [ovError, setOvError] = useState(false);

  const [pipeline, setPipeline] = useState<PipelineStats | null>(null);
  const [plLoading, setPlLoading] = useState(false);
  const [plError, setPlError] = useState(false);

  const [traces, setTraces] = useState<TraceList | null>(null);
  const [trLoading, setTrLoading] = useState(false);
  const [trError, setTrError] = useState(false);

  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const loadOverview = useCallback(async (w: MonitorWindow) => {
    setOvLoading(true);
    setOvError(false);
    try {
      setOverview(await getOverview(w));
    } catch {
      setOvError(true);
    } finally {
      setOvLoading(false);
    }
  }, []);

  const loadTraces = useCallback(async (w: MonitorWindow) => {
    setTrLoading(true);
    setTrError(false);
    try {
      setTraces(await listTraces(w));
    } catch {
      setTrError(true);
    } finally {
      setTrLoading(false);
    }
  }, []);

  const loadPipeline = useCallback(async () => {
    setPlLoading(true);
    setPlError(false);
    try {
      setPipeline(await getPipelineStats());
    } catch {
      setPlError(true);
    } finally {
      setPlLoading(false);
    }
  }, []);

  // window 切换重拉 overview + traces；pipeline 不随 window，仅首载
  useEffect(() => {
    loadOverview(win);
    loadTraces(win);
  }, [win, loadOverview, loadTraces]);
  useEffect(() => {
    loadPipeline();
  }, [loadPipeline]);

  const openTrace = async (messageId: number) => {
    setDetail(null); // 防旧详情闪烁
    setDetailLoading(true);
    try {
      setDetail(await getTrace(messageId));
    } catch {
      message.error("加载链路详情失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const traceColumns: ColumnsType<TraceListItem> = [
    { title: "#", dataIndex: "message_id", width: 72 },
    { title: "查询", dataIndex: "query", ellipsis: true },
    {
      title: "路由",
      dataIndex: "route",
      width: 100,
      render: (r: string | null) => (r ? <Tag>{r}</Tag> : <Text type="secondary">—</Text>),
    },
    { title: "耗时", dataIndex: "total_ms", width: 90, render: (v: number | null) => ms(v) },
    {
      title: "token",
      dataIndex: "tokens",
      width: 100,
      render: (t: TraceListItem["tokens"]) =>
        t && t.spent_tokens != null ? `${t.spent_tokens}${t.estimated ? "*" : ""}` : "—",
    },
    { title: "工具轮次", dataIndex: "n_tool_calls", width: 90 },
    { title: "时间", dataIndex: "created_at", width: 160, render: fmtTime },
  ];

  // Redis 两段（stream/dead）全 null 视为 Redis 不可用；单段 null 只在对应 Statistic 显「—」
  const redisDown = pipeline?.stream == null && pipeline?.dead == null;

  return (
    <div style={{ padding: 20, height: "100%", overflow: "auto" }}>
      <Card
        size="small"
        styles={{ body: { padding: "12px 20px" } }}
        style={{ marginBottom: 16 }}
      >
        <Row align="middle">
          <Col>
            <Space>
              <Text type="secondary" style={{ fontSize: 12 }}>时间窗</Text>
              <Segmented<MonitorWindow>
                value={win}
                onChange={setWin}
                options={[
                  { label: "今日", value: "today" },
                  { label: "7 天", value: "7d" },
                  { label: "全部", value: "all" },
                ]}
              />
            </Space>
          </Col>
          <Col flex="auto" />
          <Col>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {traces ? `窗内请求 ${traces.total} 条` : "—"}
            </Text>
          </Col>
        </Row>
      </Card>

      {/* 卡 1：概览 */}
      <Card size="small" title="概览" loading={ovLoading && !overview} style={{ marginBottom: 16 }}>
        {ovError ? (
          <LoadFailed />
        ) : (
          <>
            <Row gutter={24}>
              <Col><Statistic title="请求数" value={num(overview?.requests)} /></Col>
              <Col><Statistic title="代码命中率" value={pct(overview?.codenav_hit_rate)} /></Col>
              <Col><Statistic title="平均工具轮次" value={num1(overview?.avg_tool_calls)} /></Col>
              <Col><Statistic title="平均 token" value={num1(overview?.avg_tokens)} /></Col>
              <Col><Statistic title="延迟 p50" value={ms(overview?.p50_ms)} /></Col>
              <Col><Statistic title="延迟 p95" value={ms(overview?.p95_ms)} /></Col>
            </Row>
            <div style={{ marginTop: 12 }}>
              <Text type="secondary" style={{ fontSize: 12, marginInlineEnd: 8 }}>路由分布</Text>
              {overview && Object.keys(overview.routes).length > 0 ? (
                Object.entries(overview.routes).map(([route, n]) => (
                  <Tag key={route} style={{ margin: 2 }}>
                    {route} × {n}
                  </Tag>
                ))
              ) : (
                <Text type="secondary">—</Text>
              )}
            </div>
          </>
        )}
      </Card>

      {/* 卡 2：管道状态 */}
      <Card size="small" title="管道状态" loading={plLoading && !pipeline} style={{ marginBottom: 16 }}>
        {plError ? (
          <LoadFailed />
        ) : (
          <>
            <Row gutter={24} align="middle">
              <Col><Statistic title="Stream 长度" value={num(pipeline?.stream?.length)} /></Col>
              <Col><Statistic title="pending" value={num(pipeline?.stream?.pending)} /></Col>
              <Col><Statistic title="lag" value={num(pipeline?.stream?.lag)} /></Col>
              <Col><Statistic title="死信长度" value={num(pipeline?.dead?.length)} /></Col>
              <Col>
                {redisDown ? <Tag color="warning">Redis 不可用</Tag> : null}
              </Col>
            </Row>
            <div style={{ marginTop: 12 }}>
              <Text type="secondary" style={{ fontSize: 12, marginInlineEnd: 8 }}>事件状态</Text>
              {pipeline?.events ? (
                <>
                  <Tag color="success" style={{ margin: 2 }}>DONE × {pipeline.events.DONE ?? 0}</Tag>
                  <Tag color="default" style={{ margin: 2 }}>PENDING × {pipeline.events.PENDING ?? 0}</Tag>
                  <Tag color={pipeline.events.DEAD ? "error" : "default"} style={{ margin: 2 }}>
                    DEAD × {pipeline.events.DEAD ?? 0}
                  </Tag>
                </>
              ) : (
                <Text type="secondary">—</Text>
              )}
              <Text type="secondary" style={{ fontSize: 12, marginInlineStart: 12 }}>
                最近事件：{fmtTime(pipeline?.last_event_at)}
              </Text>
            </div>
          </>
        )}
      </Card>

      {/* 卡 3：全链路追溯 */}
      <Card size="small" title="全链路追溯" styles={{ body: { padding: 16 } }}>
        {trError ? (
          <LoadFailed />
        ) : (
          <div style={{ display: "flex", gap: 16 }}>
            <Table<TraceListItem>
              size="small"
              rowKey="message_id"
              loading={trLoading}
              dataSource={traces?.items ?? []}
              onRow={(r) => ({
                onClick: () => void openTrace(r.message_id),
                style: { cursor: "pointer" },
              })}
              pagination={{ pageSize: 8, showSizeChanger: false }}
              columns={traceColumns}
              style={{ width: "55%" }}
              locale={{ emptyText: "暂无追溯记录——发起一次问答后回来看" }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              {detailLoading ? <Spin /> : <TraceView detail={detail} />}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
