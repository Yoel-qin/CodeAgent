"""路径 A：向量语义召回（Milvus，设计 §11.3）。

依赖 model_server(BGE-M3) + Milvus。任一不可用则返回空，管道降级到 BM25+图遍历。
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import embedding_client, milvus_client
from app.retrieval.graph_traverse import fetch_chunks


async def vector_recall(session: AsyncSession, query: str, *, top_k: int = 20) -> list[dict]:
    if not embedding_client.enabled():
        return []
    vecs = await embedding_client.embed_texts([query])
    hits = await asyncio.to_thread(milvus_client.search, vecs[0], top_k)
    if not hits:
        return []
    score_map = {h["chunk_id"]: h["score"] for h in hits}
    chunks = await fetch_chunks(session, list(score_map.keys()))
    for c in chunks:
        c["score"] = float(score_map.get(c["chunk_id"], 0.0))
    return chunks
