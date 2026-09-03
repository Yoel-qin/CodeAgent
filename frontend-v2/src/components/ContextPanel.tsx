/**
 * 右侧常驻上下文面板（Phase 4）：展示当前聚焦引用 chunk 的调用方/被调用方/关联文档。
 * 聚焦 chunk 由 CitationCard 点击写入 Zustand（useAppStore.focused）；复用 /v1/graph/* 接口。
 */
import { useEffect, useState } from "react";
import { Empty, Spin, Tag, Typography, theme } from "antd";
import { CodeOutlined, FileTextOutlined } from "@ant-design/icons";
import { useAppStore } from "../stores/app";
import {
  getCallGraph,
  getCodeDocRelations,
  type GraphNode,
  type GraphResponse,
} from "../api/graph";

const { Text, Title } = Typography;

interface PanelData {
  callers: GraphNode[];
  callees: GraphNode[];
  docs: GraphNode[];
}

function derive(centerId: string, call: GraphResponse, rel: GraphResponse): PanelData {
  // 调用图：center 是被调用方 → 入边 source = 调用方；出边 target = 被调用方
  const callers = new Map<string, GraphNode>();
  const callees = new Map<string, GraphNode>();
  const nodeMap = new Map(call.nodes.map((n) => [n.id, n]));
  for (const e of call.edges) {
    if (e.target === centerId && nodeMap.has(e.source)) callers.set(e.source, nodeMap.get(e.source)!);
    if (e.source === centerId && nodeMap.has(e.target)) callees.set(e.target, nodeMap.get(e.target)!);
  }
  // 关联图：除 center 外的 doc 节点
  const docs = rel.nodes.filter((n) => n.type === "doc" && n.id !== centerId);
  return { callers: [...callers.values()], callees: [...callees.values()], docs };
}

function NodeLine({ n, stale }: { n: GraphNode; stale?: boolean }) {
  const { token } = theme.useToken();
  return (
    <div
      style={{
        padding: "4px 0",
        borderBottom: `1px solid ${token.colorBorderSecondary}`,
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}
    >
      <Text ellipsis className="code-font" style={{ fontSize: 12, flex: 1 }} title={n.name}>
        {n.name}
      </Text>
      {stale && <Tag color="red" style={{ margin: 0, fontSize: 10 }}>过期</Tag>}
    </div>
  );
}

function Section({ title, icon, nodes, staleKey }: {
  title: string; icon: React.ReactNode; nodes: GraphNode[]; staleKey?: (n: GraphNode) => boolean;
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      <Title level={5} style={{ marginTop: 0, marginBottom: 6, fontSize: 13 }}>
        {icon} {title} ({nodes.length})
      </Title>
      {nodes.length === 0 ? (
        <Text type="secondary" style={{ fontSize: 12 }}>无</Text>
      ) : (
        nodes.map((n) => <NodeLine key={n.id} n={n} stale={staleKey?.(n)} />)
      )}
    </div>
  );
}

export default function ContextPanel() {
  const { token } = theme.useToken();
  const focused = useAppStore((s) => s.focused);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<PanelData | null>(null);

  useEffect(() => {
    if (!focused) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    Promise.all([
      getCallGraph({ center_node: focused.chunk_id, depth: 1, direction: "BOTH", max_nodes: 20 }),
      getCodeDocRelations({ center_node: focused.chunk_id, depth: 1, max_nodes: 20 }),
    ])
      .then(([call, rel]) => {
        if (!cancelled) setData(derive(focused.chunk_id, call, rel));
      })
      .catch(() => {
        if (!cancelled) setData({ callers: [], callees: [], docs: [] });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [focused]);

  if (!focused) {
    return (
      <div style={{ padding: 16 }}>
        <Text strong>📌 当前上下文</Text>
        <div style={{ marginTop: 12, color: token.colorTextTertiary, fontSize: 13 }}>
          点击聊天中的引用卡片，此处展示其调用方 / 被调用方 / 关联文档。
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 16, height: "100%", overflow: "auto" }}>
      <Text strong>📌 当前上下文</Text>
      <div
        style={{
          marginTop: 8,
          marginBottom: 16,
          padding: "6px 8px",
          background: "rgba(255,106,0,0.1)",
          borderRadius: 6,
        }}
      >
        <Tag color={focused.type === "code" ? "blue" : "gold"} icon={focused.type === "code" ? <CodeOutlined /> : <FileTextOutlined />} style={{ margin: 0 }}>
          {focused.type === "code" ? "代码" : "文档"}
        </Tag>
        <div className="code-font" style={{ fontSize: 12, marginTop: 4, wordBreak: "break-all" }}>
          {focused.label}
        </div>
      </div>

      {loading ? (
        <div style={{ textAlign: "center", padding: 24 }}>
          <Spin />
        </div>
      ) : data && (data.callers.length || data.callees.length || data.docs.length) ? (
        <>
          <Section title="调用方（Callers）" icon={<CodeOutlined />} nodes={data.callers} staleKey={(n) => !!n.stale} />
          <Section title="被调用（Callees）" icon={<CodeOutlined />} nodes={data.callees} staleKey={(n) => !!n.stale} />
          <Section title="关联文档" icon={<FileTextOutlined />} nodes={data.docs} staleKey={(n) => !!n.stale} />
        </>
      ) : (
        <Empty description="无调用 / 关联数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </div>
  );
}
