/** 文档管理 + 资源访问 REST 客户端（Phase 1.5d，对齐后端 schemas/document.py）。 */
import { api } from "./client";

// ---- GET /documents ----

export interface DocumentItem {
  file_id: number;
  file_path: string;
  title: string | null;
  doc_type: string | null;
  file_format: string | null;
  total_pages: number | null;
  total_tables: number | null;
  total_chunks: number;
  parse_status: string | null;
  ocr_required: boolean | null;
  file_size_bytes: number | null;
  storage_path: string | null;
  created_at: string | null;
}

export interface DocumentListResponse {
  total: number;
  items: DocumentItem[];
}

// ---- POST /documents/upload ----

export interface UploadResponse {
  file_id: number;
  file_path: string;
  file_format: string | null;
  parse_status: string | null;
  total_chunks: number;
  storage_path: string | null;
  message: string;
}

// ---- GET /documents/{id}/parse-progress ----

export interface ParseProgressResponse {
  file_id: number;
  parse_status: string | null;
  parse_error: string | null;
  total_pages: number | null;
  total_tables: number | null;
  total_chunks: number | null;
  ocr_required: boolean | null;
}

// ---- GET /documents/{id}/tables ----

export interface TableListItem {
  chunk_id: string;
  table_total_rows: number | null;
  table_total_cols: number | null;
  table_description: string | null;
  is_table_fragment: boolean | null;
}

export interface TableListResponse {
  file_id: number;
  total: number;
  items: TableListItem[];
}

// ---- GET /resources/{chunk_id}/table-data ----

export interface TableDataResponse {
  chunk_id: string;
  table_data: Record<string, unknown> | null;
  table_html: string | null;
  table_description: string | null;
  table_total_rows: number | null;
  table_total_cols: number | null;
}

// ---- 调用 ----

export const listDocuments = (params: { file_format?: string; page?: number; page_size?: number } = {}) =>
  api.get<DocumentListResponse>("/v1/documents", { params }).then((r) => r.data);

export const getDocument = (fileId: number) =>
  api.get<DocumentItem>(`/v1/documents/${fileId}`).then((r) => r.data);

export const getParseProgress = (fileId: number) =>
  api.get<ParseProgressResponse>(`/v1/documents/${fileId}/parse-progress`).then((r) => r.data);

export const listDocumentTables = (fileId: number) =>
  api.get<TableListResponse>(`/v1/documents/${fileId}/tables`).then((r) => r.data);

export const deleteDocument = (fileId: number) =>
  api.delete(`/v1/documents/${fileId}`).then((r) => r.data);

export const getTableData = (chunkId: string) =>
  api.get<TableDataResponse>(`/v1/resources/${chunkId}/table-data`).then((r) => r.data);

export const uploadDocument = (file: File, docType?: string) => {
  const form = new FormData();
  form.append("file", file);
  return api
    .post<UploadResponse>("/v1/documents/upload", form, {
      params: docType ? { doc_type: docType } : undefined,
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
};
