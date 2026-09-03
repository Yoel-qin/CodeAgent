/** 引用只读预览 REST 客户端（v2：/v1/code/read 文件行窗口 + /v1/docs/section 文档段落）。 */
import { api } from "./client";

export interface CodeReadResult {
  content: string;
  total_lines: number;
  start_line: number;
  end_line: number;
  truncated: boolean;
}

export interface DocSectionResult {
  doc_name: string;
  anchor: string;
  title: string;
  content: string;
}

export const readCode = (params: {
  repo: string;
  path: string;
  start_line?: number;
  end_line?: number;
}) => api.get<CodeReadResult>("/v1/code/read", { params }).then((r) => r.data);

export const readDocSection = (params: { repo: string; doc_name: string; anchor: string }) =>
  api.get<DocSectionResult>("/v1/docs/section", { params }).then((r) => r.data);
