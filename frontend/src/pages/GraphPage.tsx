/**
 * 知识图谱页（Phase 4）：调用图 / 代码-文档关联图 / 模块依赖图。
 * Cytoscape 渲染；搜索选中心节点；点节点看详情 / 设为新中心；stale 标红。
 * 对齐后端 /v1/graph/* 4 接口。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import CytoscapeGraph, { type LayoutName } from "../components/graph/CytoscapeGraph";
import {
  getCodeDocRelations,
  getCallGraph,
  getModuleDependency,
  searchGraphNodes,
  type CallDirection,
  type GraphNode,
  type GraphResponse,
  type GraphSearchItem,
  type Granularity,
} from "../api/graph";

const { Text, Paragraph } = Typography;

type Mode = "call" | "relations" | "module";

const MODE_OPTS = [
  { label: "调用图", value: "call" },
  { label: "代码-文档关联", value: "relations" },
  { label: "模块依赖", value: "module" },
];
const LAYOUT_OPTS: { label: string; value: LayoutName }[] = [
  { label: "层次", value: "breadthfirst" },
  { label: "环形", value: "circle" },
  { label: "力导向", value: "cose" },
];

const TYPE_LABEL: Record<string, string> = {
  method: "方法", class: "类", block: "块", file: "文件", code: "代码",
  doc: "文档", module: "模块", package: "包",
};
const TYPE_COLOR: Record<string, string> = {
  blue: "#2b6cb0", green: "#38a169", orange: "#dd6b20", purple: "#805ad5", red: "#e53e3e",
};

export default function GraphPage() {
  const [mode, setMode] = useState<Mode>("call");
  const [layout, setLayout] = useState<LayoutName>("breadthfirst");
  const [granularity, setGranularity] = useState<Granularity>("MODULE");
  const [direction, setDirection] = useState<CallDirection>("BOTH");
  const [depth, setDepth] = useState(2);
  const [maxNodes, setMaxNodes] = useState(50);

  const [centerNode, setCenterNode] = useState<string | null>(null);
  const [centerLabel, setCenterLabel] = useState<string | null>(null);

  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [loading, setLoading] = useState(false);

  // 搜索
  const [q, setQ] = useState("");
  const [results, setResults] = useState<GraphSearchItem[]>([]);
  const [searching, setSearching] = useState(false);

  const [selected, setSelected] = useState<GraphNode | null>(null);

  // ---- 搜索（debounce）----
  useEffect(() => {
    const term = q.trim();
    if (!term) {
      setResults([]);
      return;
    }
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await searchGraphNodes({ q: term, limit: 15 });
        setResults(res.items);
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const needCenter = mode === "call" || mode === "relations";

  const loadGraph = useCallback(async () => {
    setLoading(true);
    try {
      let g: GraphResponse;
      if (mode === "call") {
        g = await getCallGraph({
          center_node: centerNode!, depth, direction, max_nodes: maxNodes,
        });
      } else if (mode === "relations") {
        g = await getCodeDocRelations({ center_node: centerNode!, depth, max_nodes: maxNodes });
      } else {
        g = await getModuleDependency({ granularity });
      }
      setGraph(g);
    } catch (e) {
      message.error((e as Error).message || "加载图谱失败");
      setGraph({ nodes: [], edges: [] });
    } finally {
      setLoading(false);
    }
  }, [mode, centerNode, depth, direction, maxNodes, granularity]);

  useEffect(() => {
    if (mode === "module") {
      loadGraph();
    } else if (centerNode) {
      loadGraph();
    } else {
      setGraph(null);
    }
  }, [mode, centerNode, depth, direction, maxNodes, granularity, loadGraph]);

  const pickCenter = (item: GraphSearchItem) => {
    setCenterNode(item.id);
    setCenterLabel(item.name);
    setSelected(null);
  };

  const recenter = (id: string) => {
    const node = graph?.nodes.find((n) => n.id === id);
    setCenterNode(id);
    setCenterLabel(node?.name ?? id);
    setSelected(null);
  };

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphNode>();
    graph?.nodes.forEach((n) => m.set(n.id, n));
    return m;
  }, [graph]);

  const onNodeTap = (id: string) => {
    const n = nodeById.get(id);
    if (n) setSelected(n);
  };

  const showCenterControls = needCenter;
  const empty = graph && graph.nodes.length === 0;

  return (
    <div style={{ padding: 16, height: "calc(100vh - 84px)", display: "flex", flexDirection: "column", gap: 12 }}>
      {/* 控制栏 */}
      <Card size="small" styles={{ body: { padding: "10px 16px" } }}>
        <Row gutter={12} align="middle" wrap={false}>
          <Col flex="none">
            <Segmented options={MODE_OPTS} value={mode} onChange={(v) => setMode(v as Mode)} />
          </Col>
          <Col flex="none">
            <Segmented options={LAYOUT_OPTS} value={layout} onChange={(v) => setLayout(v as LayoutName)} />
          </Col>
          {showCenterControls ? (
            <>
              {mode === "call" && (
                <Col flex="none">
                  <Select<CallDirection>
                    size="small"
                    value={direction}
                    onChange={setDirection}
                    style={{ width: 92 }}
                    options={[
                      { value: "BOTH", label: "双向" },
                      { value: "CALLERS", label: "仅调用方" },
                      { value: "CALLEES", label: "仅被调用" },
                    ]}
                  />
                </Col>
              )}
              <Col flex="none">
                <Text type="secondary" style={{ fontSize: 12 }}>深度</Text>{" "}
                <InputNumber size="small" min={1} max={5} value={depth} onChange={(v) => setDepth(v ?? 2)} />
              </Col>
              <Col flex="none">
                <Text type="secondary" style={{ fontSize: 12 }}>上限</Text>{" "}
                <InputNumber size="small" min={1} max={300} value={maxNodes} onChange={(v) => setMaxNodes(v ?? 50)} />
              </Col>
            </>
          ) : (
            <Col flex="none">
              <Text type="secondary" style={{ fontSize: 12 }}>粒度</Text>{" "}
              <Select<Granularity>
                size="small"
                value={granularity}
                onChange={setGranularity}
                style={{ width: 96 }}
                options={[
                  { value: "MODULE", label: "模块" },
                  { value: "PACKAGE", label: "包" },
                  { value: "CLASS", label: "类" },
                ]}
              />
            </Col>
          )}
          <Col flex="auto" />
          <Col flex="none">
            <Space>
              {graph?.truncated && <Tag color="warning">已截断</Tag>}
              {graph && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {graph.nodes.length} 节点 / {graph.edges.length} 边
                </Text>
              )}
              <Button size="small" icon={<ReloadOutlined />} onClick={loadGraph} loading={loading}>刷新</Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 12 }}>
        {/* 左：搜索 / 中心选择 */}
        <Card
          size="small"
          title={needCenter ? "选择中心节点" : "说明"}
          style={{ width: 264, overflow: "auto", flex: "none" }}
          styles={{ body: { padding: 12 } }}
        >
          {needCenter ? (
            <>
              <Input.Search
                size="small"
                placeholder="搜索类名 / 方法名 / 文档"
                prefix={<SearchOutlined />}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                loading={searching}
                allowClear
              />
              {centerNode && (
                <div style={{ marginTop: 10, padding: "6px 8px", background: "rgba(255,106,0,0.1)", borderRadius: 6 }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>当前中心</Text>
                  <div className="code-font" style={{ fontSize: 12, wordBreak: "break-all" }}>{centerLabel}</div>
                </div>
              )}
              <div style={{ marginTop: 10 }}>
                {results.length === 0 ? (
                  q.trim() ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {searching ? "搜索中…" : "无匹配，可输入类名/方法名"}
                    </Text>
                  ) : (
                    <Text type="secondary" style={{ fontSize: 12 }}>输入关键词搜索节点</Text>
                  )
                ) : (
                  results.map((r) => (
                    <div
                      key={`${r.type}:${r.id}`}
                      onClick={() => pickCenter(r)}
                      style={{
                        padding: "5px 6px", cursor: "pointer", borderRadius: 4,
                        borderBottom: "1px solid rgba(127,127,127,0.15)",
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(127,127,127,0.15)")}
                      onMouseLeave={(e) => (e.currentTarget.style.background = "")}
                    >
                      <Tag color={r.type === "doc" ? "green" : r.type === "class" ? "orange" : "blue"} style={{ margin: 0 }}>
                        {TYPE_LABEL[r.type] || r.type}
                      </Tag>{" "}
                      <Text ellipsis className="code-font" style={{ fontSize: 12 }}>{r.name}</Text>
                    </div>
                  ))
                )}
              </div>
            </>
          ) : (
            <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
              模块依赖图按调用边聚合：节点为{granularity === "CLASS" ? "类" : granularity === "PACKAGE" ? "包" : "模块"}，
              边权重 = 跨组调用次数（自环已去除）。
            </Paragraph>
          )}

          {/* 图例 */}
          <div style={{ marginTop: 16, paddingTop: 10, borderTop: "1px solid rgba(127,127,127,0.2)" }}>
            <Text type="secondary" style={{ fontSize: 11 }}>图例</Text>
            <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 6 }}>
              <Legend color={TYPE_COLOR.blue} label="方法/代码" />
              <Legend color={TYPE_COLOR.orange} label="类" />
              <Legend color={TYPE_COLOR.green} label="文档" />
              <Legend color={TYPE_COLOR.purple} label="模块/包" />
              <Legend color={TYPE_COLOR.red} label="过期" />
            </div>
          </div>
        </Card>

        {/* 主：图画布 */}
        <Card size="small" bodyStyle={{ padding: 0, height: "100%" }} style={{ flex: 1, minWidth: 0 }}>
          <div style={{ position: "relative", height: "100%" }}>
            {loading && (
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", zIndex: 5 }}>
                <Spin />
              </div>
            )}
            {graph && graph.nodes.length > 0 ? (
              <CytoscapeGraph
                graph={graph}
                layout={layout}
                rootId={needCenter ? centerNode : undefined}
                onNodeTap={onNodeTap}
              />
            ) : (
              !loading && (
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Empty
                    description={
                      needCenter && !centerNode
                        ? "请先在左侧搜索并选择一个中心节点"
                        : empty
                          ? "该中心无关联图数据"
                          : "无数据"
                    }
                  />
                </div>
              )
            )}
          </div>
        </Card>
      </div>

      {/* 节点详情抽屉 */}
      <Drawer
        title="节点详情"
        open={!!selected}
        onClose={() => setSelected(null)}
        width={420}
        destroyOnClose
      >
        {selected && (
          <>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="名称">
                <Text className="code-font">{selected.name}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="类型">
                <Tag>{TYPE_LABEL[selected.type] || selected.type}</Tag>
              </Descriptions.Item>
              {selected.class_name && (
                <Descriptions.Item label="类">{selected.class_name}</Descriptions.Item>
              )}
              {selected.method_name && (
                <Descriptions.Item label="方法">{selected.method_name}</Descriptions.Item>
              )}
              {selected.module && (
                <Descriptions.Item label="模块">{selected.module}</Descriptions.Item>
              )}
              {selected.file_path && (
                <Descriptions.Item label="文件">
                  <Text code style={{ fontSize: 12 }}>{selected.file_path}</Text>
                </Descriptions.Item>
              )}
              {selected.heading_path && selected.heading_path.length > 0 && (
                <Descriptions.Item label="章节">
                  {selected.heading_path.join(" / ")}
                </Descriptions.Item>
              )}
              {selected.class_count != null && (
                <Descriptions.Item label="类数">{selected.class_count}</Descriptions.Item>
              )}
              <Descriptions.Item label="过期">
                {selected.stale ? (
                  <Tag color="red">是{selected.stale_reason ? `：${selected.stale_reason}` : ""}</Tag>
                ) : (
                  <Tag>否</Tag>
                )}
              </Descriptions.Item>
            </Descriptions>
            {needCenter && (
              <Button type="primary" block style={{ marginTop: 16 }} onClick={() => recenter(selected.id)}>
                以此节点为中心重新展开
              </Button>
            )}
          </>
        )}
      </Drawer>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11 }}>
      <span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 3, background: color }} />
      <Text type="secondary" style={{ fontSize: 11 }}>{label}</Text>
    </span>
  );
}
