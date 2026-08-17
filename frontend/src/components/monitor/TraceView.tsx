/**
 * M41 单请求全链路追溯视图：内联 SVG waterfall（x=start_ms / width=duration_ms，
 * 色按 kind；仓库零图表库惯例同 EvalPage Sparkline）+ AntD Tree（attrs/异常详情）。
 * 旧格式（legacy=true）显示「旧格式 · 部分链路」Tag。
 */
import { Empty, Tag, Tree, Typography } from "antd";
import type { DataNode } from "antd/es/tree";
import { useMemo } from "react";
import type { TraceDetail, TraceSpan } from "../../api/monitor";

const KIND_COLORS: Record<string, string> = {
  request: "#5b8ff9",
  intent: "#9270ca",
  route: "#d0b03c",
  agent: "#f6903d",
  collab: "#f6903d",
  tool: "#63c2a8",
  retrieval: "#5ad8a6",
  llm: "#e8684a",
  degrade: "#c0c0c0",
};

const kindColor = (k: string) => KIND_COLORS[k] ?? "#999";
const fmtMs = (v: number | null | undefined) =>
  v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`;

function depthOf(spans: TraceSpan[]): Map<number, number> {
  // parent 链算缩进深度（平面列表 → 视图层级）
  const byId = new Map(spans.map((s) => [s.span_id, s]));
  const depth = new Map<number, number>();
  const d = (s: TraceSpan): number => {
    if (depth.has(s.span_id)) return depth.get(s.span_id)!;
    const p = s.parent_id != null ? byId.get(s.parent_id) : undefined;
    const v = p ? d(p) + 1 : 0;
    depth.set(s.span_id, v);
    return v;
  };
  spans.forEach(d);
  return depth;
}

export default function TraceView({ detail }: { detail: TraceDetail | null }) {
  const depths = useMemo(() => depthOf(detail?.spans ?? []), [detail]);
  if (!detail) return <Empty description="选择左侧请求查看全链路" />;
  const max = Math.max(
    1,
    ...detail.spans.map((s) => s.start_ms + (s.duration_ms ?? 0)),
  );
  const treeData: DataNode[] = detail.spans.map((s) => ({
    key: s.span_id,
    title: (
      <span>
        <Tag color={s.status === "error" ? "red" : "blue"} style={{ marginInlineEnd: 6 }}>
          {s.kind}
        </Tag>
        {s.name} · {fmtMs(s.duration_ms)}
        {s.tokens
          ? ` · ${s.tokens.prompt}+${s.tokens.completion} tok${s.tokens.estimated ? "（估）" : ""}`
          : ""}
        {s.error ? <Typography.Text type="danger"> · {s.error}</Typography.Text> : null}
      </span>
    ),
  }));

  return (
    <div>
      <Typography.Text strong>
        #{detail.log_id} · {detail.query}
      </Typography.Text>
      {detail.legacy && (
        <Tag color="orange" style={{ marginInlineStart: 8 }}>
          旧格式 · 部分链路
        </Tag>
      )}
      {detail.summary && (
        <Typography.Text type="secondary" style={{ marginInlineStart: 12 }}>
          汇总 {fmtMs(detail.summary.total_ms)}
          {detail.summary.tokens
            ? ` · ${detail.summary.tokens.prompt}+${detail.summary.tokens.completion} tok` +
              (detail.summary.tokens.estimated ? "（含估算）" : "")
            : ""}
        </Typography.Text>
      )}
      <svg
        width="100%"
        viewBox={`0 0 600 ${Math.max(1, detail.spans.length) * 22}`}
        style={{ display: "block", margin: "12px 0" }}
      >
        {detail.spans.map((s, i) => {
          const w = ((s.duration_ms ?? 0) / max) * 520;
          const x = 60 + (s.start_ms / max) * 520 + (depths.get(s.span_id) ?? 0) * 4;
          return (
            <g key={s.span_id}>
              <text x={4} y={i * 22 + 14} fontSize={10} fill="#666">
                {`${"  ".repeat(depths.get(s.span_id) ?? 0)}${s.name}`.slice(0, 14)}
              </text>
              <rect
                x={x}
                y={i * 22 + 4}
                width={Math.max(2, w)}
                height={14}
                rx={2}
                fill={kindColor(s.kind)}
                opacity={s.status === "error" ? 1 : 0.85}
              />
            </g>
          );
        })}
      </svg>
      <Tree treeData={treeData} defaultExpandAll blockNode />
    </div>
  );
}
