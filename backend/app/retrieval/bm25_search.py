"""路径 B：Elasticsearch BM25 召回（设计 §11.3）。asyncio.to_thread 包同步 ES 客户端。"""
from __future__ import annotations

import asyncio

from app.clients import es_client
from app.retrieval.query_understanding import extract_query_terms


async def bm25_recall(query: str, *, top_k: int = 20) -> list[dict]:
    """返回 ES BM25 命中（terms on keywords + match on content）。失败抛异常由上层降级。"""
    terms = extract_query_terms(query)
    hits = await asyncio.to_thread(es_client.search, terms, query, top_k)
    return hits
