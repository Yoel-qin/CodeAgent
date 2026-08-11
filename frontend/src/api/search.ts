/** 全局搜索（⌘K）REST 客户端，对齐后端 schemas/search.py。 */
import { api } from "./client";

export interface SearchItem {
  chunk_id: string;
  kind: "code" | "doc";
  label: string;
  snippet: string;
  score: number;
}

export interface SearchResponse {
  q: string;
  total: number;
  items: SearchItem[];
}

/** GET /v1/search —— 关键词级 chunk 检索（code+doc），供 ⌘K palette。 */
export const searchKb = (
  q: string,
  kind?: "code" | "doc",
  top_k = 12,
): Promise<SearchResponse> =>
  api.get<SearchResponse>("/v1/search", { params: { q, kind, top_k } }).then((r) => r.data);
