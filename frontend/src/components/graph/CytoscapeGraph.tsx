/**
 * Cytoscape.js 图渲染器（Phase 4）。
 * 包 react-cytoscapejs；按 node.type/stale 着色；hover 高亮相邻、tap 回调、stale 标红。
 * 切换布局/图时通过 key 重新挂载，确保 layout 干净应用。
 */
import { useMemo } from "react";
import CytoscapeComponent from "react-cytoscapejs";
import type { ElementDefinition, LayoutOptions } from "cytoscape";
import type { GraphResponse } from "../../api/graph";

export type LayoutName = "breadthfirst" | "circle" | "cose";

interface CssRule {
  selector: string;
  style: Record<string, unknown>;
}

interface Props {
  graph: GraphResponse;
  layout: LayoutName;
  /** breadthfirst 的根节点 id（仅当存在于节点集时生效） */
  rootId?: string | null;
  onNodeTap?: (id: string) => void;
  height?: number | string;
}

const STYLESHEET: CssRule[] = [
  {
    selector: "node",
    style: {
      label: "data(name)",
      "text-valign": "center",
      "text-halign": "center",
      "text-wrap": "wrap",
      "text-max-width": "78px",
      "font-size": "10px",
      color: "#fff",
      "text-outline-color": "#1f2933",
      "text-outline-width": 2,
      width: "44px",
      height: "44px",
      "border-width": 2,
      "border-color": "#3a4350",
      "background-color": "#2b6cb0", // 默认 code/method 蓝
      "z-index": 10,
    },
  },
  { selector: 'node[type="doc"]', style: { "background-color": "#38a169" } }, // 绿
  { selector: 'node[type="class"], node[type="block"], node[type="file"]', style: { "background-color": "#dd6b20" } }, // 橙
  { selector: 'node[type="module"], node[type="package"]', style: { "background-color": "#805ad5" } }, // 紫
  {
    selector: "node.stale",
    style: { "border-color": "#e53e3e", "border-width": 3, "background-color": "#9b2c2c" },
  },
  {
    selector: "edge",
    style: {
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.8,
      "line-color": "#5a6675",
      "target-arrow-color": "#5a6675",
      width: 2,
      "text-background-color": "#1f2933",
      "text-background-opacity": 0.8,
      "text-background-padding": 2,
    },
  },
  {
    selector: "edge.stale",
    style: { "line-color": "#e53e3e", "target-arrow-color": "#e53e3e", width: 2.5 },
  },
  { selector: ".highlighted", style: { opacity: 1 } },
  { selector: ".faded", style: { opacity: 0.12 } },
];

function buildElements(graph: GraphResponse): ElementDefinition[] {
  const nodes: ElementDefinition[] = graph.nodes.map((n) => ({
    data: {
      id: n.id,
      name: n.name,
      type: n.type,
      stale: !!n.stale,
    },
    classes: n.stale ? "stale" : "",
  }));
  const edges: ElementDefinition[] = graph.edges.map((e, i) => ({
    data: {
      id: `e${i}_${e.source}__${e.target}`,
      source: e.source,
      target: e.target,
      type: e.type,
      weight: e.weight,
      stale: !!e.stale,
    },
    classes: e.stale ? "stale" : "",
  }));
  return [...nodes, ...edges];
}

function buildLayout(layout: LayoutName, rootId?: string | null): LayoutOptions {
  if (layout === "circle") {
    return { name: "circle", padding: 30, animate: false } as unknown as LayoutOptions;
  }
  if (layout === "cose") {
    return {
      name: "cose",
      animate: false,
      nodeRepulsion: () => 8000,
      idealEdgeLength: () => 90,
      padding: 30,
    } as unknown as LayoutOptions;
  }
  // breadthfirst（层次）
  const opts: Record<string, unknown> = {
    name: "breadthfirst",
    directed: true,
    padding: 30,
    spacingFactor: 1.2,
    animate: false,
  };
  if (rootId) {
    opts.roots = `#${rootId}`;
  }
  return opts as unknown as LayoutOptions;
}

export default function CytoscapeGraph({ graph, layout, rootId, onNodeTap, height = "100%" }: Props) {
  const elements = useMemo(() => buildElements(graph), [graph]);

  // 仅当 rootId 真实存在于节点集时用作 breadthfirst 根
  const effectiveRoot =
    rootId && graph.nodes.some((n) => n.id === rootId) ? rootId : null;
  const finalLayout = useMemo(
    () => buildLayout(layout, effectiveRoot),
    [layout, effectiveRoot],
  );

  const handleCy = (cy: import("cytoscape").Core) => {
    cy.on("tap", "node", (evt) => onNodeTap?.(evt.target.id()));
    // hover 高亮相邻、其余淡化
    cy.on("mouseover", "node", (evt) => {
      const ele = evt.target;
      cy.elements().addClass("faded");
      ele.removeClass("faded").addClass("highlighted");
      ele.neighborhood().removeClass("faded").addClass("highlighted");
    });
    cy.on("mouseout", "node", () => {
      cy.elements().removeClass("faded highlighted");
    });
  };

  // 切换布局/图签名 → 重新挂载，干净应用 layout
  const remountKey = `${layout}:${effectiveRoot ?? ""}:${graph.nodes.length}:${graph.edges.length}`;

  return (
    <div style={{ width: "100%", height: typeof height === "number" ? `${height}px` : height }}>
      <CytoscapeComponent
        key={remountKey}
        elements={elements}
        layout={finalLayout}
        stylesheet={STYLESHEET}
        cy={handleCy}
        minZoom={0.3}
        maxZoom={3}
        wheelSensitivity={0.3}
        style={{ width: "100%", height: "100%", background: "#161b22" }}
      />
    </div>
  );
}
