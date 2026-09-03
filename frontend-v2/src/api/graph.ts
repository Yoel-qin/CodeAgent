/** v2 调用图 REST 客户端（/v1/graph/*；响应形状与 CytoscapeGraph 兼容）。 */
import { api } from "./client";

export type CallDirection = "BOTH" | "CALLERS" | "CALLEES";

export interface GraphNode {
  id: string;
  name: string;
  type: "method" | "class" | "module";
  class_name?: string | null;
  method_name?: string | null;
  module?: string | null;
  file_path?: string | null;
  // v2 后端不下发（恒 falsy）；保留可选字段以零改复用 CytoscapeGraph 的 stale 标红逻辑。
  stale?: boolean | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  weight: number;
  stale?: boolean | null;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  center?: string | null;
  truncated?: boolean;
}

export interface GraphSearchItem {
  id: string;
  name: string;
  type: string;
  class_name?: string | null;
  method_name?: string | null;
  module?: string | null;
  file_path?: string | null;
}

export const searchGraphNodes = (params: { q: string; repo: string; limit?: number }) =>
  api.get<{ items: GraphSearchItem[] }>("/v1/graph/search", { params }).then((r) => r.data);

export const getCallGraph = (params: {
  repo: string;
  class_name: string;
  method?: string;
  direction?: CallDirection;
  depth?: number;
  max_nodes?: number;
}) =>
  api.get<GraphResponse>("/v1/graph/call-graph", { params }).then((r) => r.data);

export const getModuleDeps = (params: { repo: string; max_nodes?: number }) =>
  api.get<GraphResponse>("/v1/graph/module-deps", { params }).then((r) => r.data);
