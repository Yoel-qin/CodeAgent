/** v2 文档 REST 客户端（/v1/documents，只读浏览——入库走 ingest CLI / git webhook 管道）。 */
import { api } from "./client";

export interface DocumentItem {
  id: number;
  repo: string;
  doc_name: string;
  module: string | null;
  doc_type: string;
  status: string;
  section_count: number;
  created_at: string | null;
}

export interface DocSectionItem {
  id: number;
  anchor: string;
  title: string;
  level: number | null;
  kind: string;
  token_count: number;
  page: number | null;
  content: string;
}

export const listDocuments = (params?: { repo?: string; limit?: number; offset?: number }) =>
  api
    .get<{ total: number; items: DocumentItem[] }>("/v1/documents", { params })
    .then((r) => r.data);

// 详情的 document 不带 section_count（仅列表带，见 document_service.get_document_with_sections）
// ——显式收窄，避免声明必填却恒缺的陷阱字段。
export type DocumentDetail = Omit<DocumentItem, "section_count"> & { section_count?: number };

export const getDocumentSections = (id: number) =>
  api
    .get<{ document: DocumentDetail; sections: DocSectionItem[] }>(
      `/v1/documents/${id}/sections`,
    )
    .then((r) => r.data);
