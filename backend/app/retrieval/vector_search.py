"""路径 A：向量语义召回（Milvus，设计 §11.3）。

按 embedding_strategy 双框架：
  unified → 查询用 BGE-M3 编码，搜单一 coderag_vectors（kind 过滤可选，混检 code+doc）。
  dual    → 查询分别用 CodeBERT(→code_vectors) 与 BGE-M3(→doc_vectors) 编码，两路合并；
            + M25：额外用 BGE-M3 查询向量检索代码镜像索引 code_vectors_bge，让多语言 BGE-M3
              也能找回代码（修 CodeBERT 对中文 NL 代码查询召回弱——见 docs/嵌入向量方案.md §二.3）。
任一编码器/Milvus 不可用 → 该路返回空，管道降级到 BM25+图遍历。
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import embedding_client, milvus_client
from app.core.config import settings
from app.retrieval.graph_traverse import fetch_chunks


async def vector_recall(session: AsyncSession, query: str, *, top_k: int = 20,
                        allowed_kinds: set[str] | None = None) -> list[dict]:
    # M45：allowed_kinds 非 None 时——unified 传 expr 过滤；dual 跳过被拒的 collection
    # （code 无权限 → 不搜 code_vectors/code_vectors_bge；doc 侧 chunk 运行时 kind 一律
    #  "doc"（媒体不分），故 doc collection 的去留由 "doc" 是否在白名单决定）。
    vecs = await embedding_client.query_embed(query)
    allowed = allowed_kinds  # None = 不过滤
    hits: list[dict] = []
    for role, vec in vecs.items():
        if vec is None:
            continue
        if allowed is not None and role != "unified" and role not in allowed:
            continue  # dual：code/doc collection 按 role 粗过滤
        kind = None if role == "unified" else role
        expr_kinds = sorted(allowed) if (allowed is not None and kind is None) else None
        try:
            # 位置参数（避免 asyncio.to_thread 关键字参数陷阱，见 CLAUDE.md）
            h = await asyncio.to_thread(
                milvus_client.search, settings.embedding_strategy, kind, vec, top_k, expr_kinds,
            )
            hits.extend(h)
        except Exception:
            continue
    # M25：dual 模式额外用 BGE-M3 查询向量（vecs["doc"]）检索代码镜像索引 code_vectors_bge——
    # 让多语言 BGE-M3 也能找回代码（CodeBERT 无中文，对中文 NL 代码查询召回弱）。复用已算好的 doc 向量不重算；
    # unified 无 vecs["doc"] → 跳过；空/未建 collection → search 返 [] no-op；top_k 位置参。
    if (settings.embedding_strategy == "dual"
            and settings.dual_code_bgem3_enabled
            and vecs.get("doc") is not None
            and (allowed is None or "code" in allowed)):   # M45：code 镜像索引同受 code 权限门
        try:
            h = await asyncio.to_thread(
                milvus_client.search, settings.embedding_strategy, "code_bge", vecs["doc"], top_k,
            )
            hits.extend(h)
        except Exception:
            pass
    if not hits:
        return []
    score_map = {h["chunk_id"]: h["score"] for h in hits}
    chunks = await fetch_chunks(session, list(score_map.keys()))
    for c in chunks:
        c["score"] = float(score_map.get(c["chunk_id"], 0.0))
    return chunks
