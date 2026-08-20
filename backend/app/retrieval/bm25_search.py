"""路径 B：Elasticsearch BM25 召回（设计 §11.3）。asyncio.to_thread 包同步 ES 客户端。
"""
from __future__ import annotations

import asyncio

from app.clients import es_client
from app.retrieval.query_understanding import extract_query_terms


async def bm25_recall(query: str, *, top_k: int = 20,
                     allowed_kinds: set[str] | None = None) -> list[dict]:
    """返回 ES BM25 命中（terms on keywords + match on content）。
    M45：allowed_kinds 非 None → 传 kinds 过滤（RBAC 检索过滤）。
    失败抛异常由上层降级。"""
    terms = extract_query_terms(query)
    kinds = sorted(allowed_kinds) if allowed_kinds is not None else None
    # 位置参数（避免 asyncio.to_thread 关键字参数陷阱，见 CLAUDE.md）
    hits = await asyncio.to_thread(es_client.search, terms, query, top_k, kinds)
    return hits
