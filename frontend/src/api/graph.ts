/** 知识图谱模块 REST 客户端（对齐后端 schemas/graph.py / api接口清单 §四）。 */
import { api } from "./client";

export type CallDirection = "BOTH" | "CALLERS" | "CALLEES";
export type Granularity = "MODULE" | "PACKAGE" | "CLASS";
export type NodeKind = "class" | "method" | "doc";

export interface GraphNode {
  id: string;
  name: string;
  type: string; // method/class/block/file/code/doc/module/package
  module?: string | null;
  class_name?: string | null;
  method_name?: string | null;
  file_path?: string | null;
  heading_path?: string[];
  stale?: boolean;
  stale_reason?: string | null;
  class_count?: number | null;
  depth?: number | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string; // CALLS / DOCUMENTED_BY / DEPENDS_ON
  weight: number;
  stale?: boolean;
  stale_reason?: string | null;
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
  module?: string | null;
  class_name?: string | null;
  file_path?: string | null;
  heading_path?: string[];
}

export interface GraphSearchResponse {
  items: GraphSearchItem[];
}

// ---- 调用图 ----
export const getCallGraph = (params: {
  center_node: string;
  depth?: number;
  direction?: CallDirection;
  max_nodes?: number;
}) => api.get<GraphResponse>("/v1/graph/call-graph", { params }).then((r) => r.data);

// ---- 代码-文档关联图 ----
export const getCodeDocRelations = (params: {
  center_node: string;
  depth?: number;
  include_stale_only?: boolean;
  max_nodes?: number;
}) => api.get<GraphResponse>("/v1/graph/code-doc-relations", { params }).then((r) => r.data);

// ---- 模块依赖图 ----
export const getModuleDependency = (params: { granularity?: Granularity } = {}) =>
  api.get<GraphResponse>("/v1/graph/module-dependency", { params }).then((r) => r.data);

// ---- 图谱节点搜索 ----
export const searchGraphNodes = (params: { q: string; node_type?: NodeKind; limit?: number }) =>
  api.get<GraphSearchResponse>("/v1/graph/search", { params }).then((r) => r.data);
