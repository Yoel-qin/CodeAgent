/**
 * 调用图页（M6 v2）：调用图 / 模块依赖两模式（v2 无 chunk_relation，「代码-文档关联」已删）。
 * Cytoscape 渲染；repo 过滤 + 搜索选中心（类/方法）；点节点看详情 / 设为新中心。
 * 对齐后端 /v1/graph/search|call-graph|module-deps（响应形状冻结，CytoscapeGraph 零改复用）。
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
import { listRepos } from "../api/repos";
import {
  getCallGraph,
  getModuleDeps,
  searchGraphNodes,
  type CallDirection,
  type GraphNode,
  type GraphResponse,
  type GraphSearchItem,
} from "../api/graph";

const { Text, Paragraph } = Typography;

type Mode = "call" | "module";

/** 调用图中心 = 一次实体搜索的选中项（class_name 必有，method_name 空 = 整类展开）。 */
interface Center {
  class_name: string;
  method_name?: string | null;
  label: string;
}

const MODE_OPTS = [
  { label: "调用图", value: "call" },
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
const TYPE_TAG: Record<string, string> = { method: "blue", class: "orange", module: "purple" };
const TYPE_COLOR: Record<string, string> = { blue: "#2b6cb0", orange: "#dd6b20", purple: "#805ad5" };

export default function GraphPage() {
  const [mode, setMode] = useState<Mode>("call");
  const [layout, setLayout] = useState<LayoutName>("breadthfirst");
  const [repos, setRepos] = useState<string[]>([]);
  const [repo, setRepo] = useState<string | undefined>(undefined);
  const [direction, setDirection] = useState<CallDirection>("BOTH");
  const [depth, setDepth] = useState(2);
  const [maxNodes, setMaxNodes] = useState(50);

  const [center, setCenter] = useState<Center | null>(null);

  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [loading, setLoading] = useState(false);

  // 搜索
  const [q, setQ] = useState("");
  const [results, setResults] = useState<GraphSearchItem[]>([]);
  const [searching, setSearching] = useState(false);

  const [selected, setSelected] = useState<GraphNode | null>(null);

  // ---- 仓库列表（默认取第一个选项）----
  useEffect(() => {
    listRepos()
      .then((r) => {
        setRepos(r.items);
        setRepo((cur) => cur ?? r.items[0]);
      })
      .catch(() => setRepos([]));
  }, []);

  // ---- 搜索（debounce；实体搜索必须带 repo）----
  useEffect(() => {
    const term = q.trim();
    if (!term || !repo) {
      setResults([]);
      return;
    }
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await searchGraphNodes({ q: term, repo, limit: 15 });
        setResults(res.items);
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [q, repo]);

  const needCenter = mode === "call";

  const loadGraph = useCallback(async () => {
    if (!repo) return;
    if (needCenter && !center) return;
    setLoading(true);
    try {
      const g =
        mode === "call"
          ? await getCallGraph({
              repo,
              class_name: center!.class_name,
              method: center!.method_name ?? undefined,
              direction,
              depth,
              max_nodes: maxNodes,
            })
          : await getModuleDeps({ repo, max_nodes: maxNodes });
      setGraph(g);
    } catch (e) {
      message.error((e as Error).message || "加载图谱失败");
      setGraph({ nodes: [], edges: [] });
    } finally {
      setLoading(false);
    }
  }, [mode, needCenter, repo, center, direction, depth, maxNodes]);

  useEffect(() => {
    if (!repo) {
      setGraph(null);
      return;
    }
    if (!needCenter || center) {
      void loadGraph();
    } else {
      setGraph(null);
    }
  }, [repo, needCenter, center, direction, depth, maxNodes, loadGraph]);

  const changeRepo = (r: string) => {
    setRepo(r);
    setCenter(null); // 中心实体属于旧仓库，换仓库后作废
    setResults([]);
    setGraph(null);
  };

  const pickCenter = (item: GraphSearchItem) => {
    setCenter({
      class_name: item.class_name!,
      method_name: item.method_name ?? undefined,
      label: item.name,
    });
    setSelected(null);
  };

  const recenter = (n: GraphNode) => {
    setCenter({ class_name: n.class_name ?? n.name, method_name: n.method_name, label: n.name });
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

  const empty = graph && graph.nodes.length === 0;

  return (
    <div style={{ padding: 16, height: "calc(100vh - 84px)", display: "flex", flexDirection: "column", gap: 12 }}>
      {/* 控制栏 */}
      <Card size="small" styles={{ body: { padding: "10px 16px" } }}>
        <Row gutter={12} align="middle" wrap={false}>
          <Col flex="none">
            <Select
              size="small"
              showSearch
              optionFilterProp="label"
              placeholder="仓库"
              value={repo}
              onChange={changeRepo}
              style={{ width: 150 }}
              options={repos.map((r) => ({ value: r, label: r }))}
            />
          </Col>
          <Col flex="none">
            <Segmented options={MODE_OPTS} value={mode} onChange={(v) => setMode(v as Mode)} />
          </Col>
          <Col flex="none">
            <Segmented options={LAYOUT_OPTS} value={layout} onChange={(v) => setLayout(v as LayoutName)} />
          </Col>
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
          {mode === "call" && (
            <Col flex="none">
              <Text type="secondary" style={{ fontSize: 12 }}>深度</Text>{" "}
              <InputNumber size="small" min={1} max={5} value={depth} onChange={(v) => setDepth(v ?? 2)} />
            </Col>
          )}
          <Col flex="none">
            <Text type="secondary" style={{ fontSize: 12 }}>上限</Text>{" "}
            <InputNumber size="small" min={1} max={300} value={maxNodes} onChange={(v) => setMaxNodes(v ?? 50)} />
          </Col>
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
                placeholder="搜索类名 / 方法名"
                prefix={<SearchOutlined />}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                loading={searching}
                allowClear
              />
              {center && (
                <div style={{ marginTop: 10, padding: "6px 8px", background: "rgba(255,106,0,0.1)", borderRadius: 6 }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>当前中心</Text>
                  <div className="code-font" style={{ fontSize: 12, wordBreak: "break-all" }}>{center.label}</div>
                </div>
              )}
              <div style={{ marginTop: 10 }}>
                {!repo ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>先在上方选择仓库</Text>
                ) : results.length === 0 ? (
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
                      <Tag color={TYPE_TAG[r.type] || "default"} style={{ margin: 0 }}>
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
              模块依赖图按跨 module 调用边聚合：节点为模块，边权重 = 跨模块调用次数（同模块内调用不计）。
            </Paragraph>
          )}

          {/* 图例 */}
          <div style={{ marginTop: 16, paddingTop: 10, borderTop: "1px solid rgba(127,127,127,0.2)" }}>
            <Text type="secondary" style={{ fontSize: 11 }}>图例</Text>
            <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 6 }}>
              <Legend color={TYPE_COLOR.blue} label="方法" />
              <Legend color={TYPE_COLOR.orange} label="类" />
              <Legend color={TYPE_COLOR.purple} label="模块" />
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
                rootId={needCenter ? graph?.center : undefined}
                onNodeTap={onNodeTap}
              />
            ) : (
              !loading && (
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Empty
                    description={
                      !repo
                        ? "请先选择仓库"
                        : needCenter && !center
                          ? "请先在左侧搜索并选择一个中心节点"
                          : empty
                            ? needCenter
                              ? "该中心无关联图数据"
                              : "该仓库无跨模块调用"
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
            </Descriptions>
            {needCenter && (
              <Button type="primary" block style={{ marginTop: 16 }} onClick={() => recenter(selected)}>
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
