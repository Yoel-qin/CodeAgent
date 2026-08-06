"""路径 A：向量语义召回（Milvus，设计 §11.3）。

按 embedding_strategy 双框架：
  unified → 查询用 BGE-M3 编码，搜单一 coderag_vectors（kind 过滤可选，混检 code+doc）。
  dual    → 查询分别用 CodeBERT(→code_vectors) 与 BGE-M3(→doc_vectors) 编码，两路合并。
任一编码器/Milvus 不可用 → 该路返回空，管道降级到 BM25+图遍历。
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import embedding_client, milvus_client
from app.core.config import settings
from app.retrieval.graph_traverse import fetch_chunks


async def vector_recall(session: AsyncSession, query: str, *, top_k: int = 20) -> list[dict]:
    # query_embed 按 strategy 返回 {role: vec|None}：unified→{"unified":..}，dual→{"code":..,"doc":..}
    vecs = await embedding_client.query_embed(query)
    hits: list[dict] = []
    for role, vec in vecs.items():
        if vec is None:
            continue
        kind = None if role == "unified" else role  # unified 混检；dual 按 role=code/doc
        try:
            # 位置参数（避免 asyncio.to_thread 关键字参数陷阱，见 CLAUDE.md）
            h = await asyncio.to_thread(
                milvus_client.search, settings.embedding_strategy, kind, vec, top_k,
            )
            hits.extend(h)
        except Exception:
            continue
    if not hits:
        return []
    score_map = {h["chunk_id"]: h["score"] for h in hits}
    chunks = await fetch_chunks(session, list(score_map.keys()))
    for c in chunks:
        c["score"] = float(score_map.get(c["chunk_id"], 0.0))
    return chunks
