"""检索管道编排（设计 §11）。

    Stage 0 查询理解
      → 四路召回（A 向量 / B ES BM25（不可用降级 PG 词法）/ D 图遍历；C 图向量 Phase 5）
      → Stage 1 RRF 融合去重（§11.4）
      → Stage 2 粗排 bge-reranker-base（§11.5）
      → Stage 3 精排 bge-reranker-v2-m3（§11.6；图特征融合待 Phase 5）

每路召回与整段精排均独立 try/except：向量/BM25 不可用退回词法召回，
精排不可用（无 Key / API 失败）退回 RRF 排序——主链路永不因可选项中断。
"""
from __future__ import annotations

import time

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import reranker_client
from app.core.config import settings
from app.retrieval.bm25_search import bm25_recall
from app.retrieval.fusion import DEFAULT_WEIGHTS, rrf_fuse
from app.retrieval.graph_traverse import graph_recall
from app.retrieval.lexical_search import lexical_recall
from app.retrieval.query_understanding import extract_query_terms
from app.retrieval.reranker import rerank_stage
from app.retrieval.vector_search import vector_recall

_SEED_DEPTH = 1
_GRAPH_MAX_NODES = 12
_SEED_TOP = 5  # 取代码召回前 N 条作为图遍历种子


class RetrievalPipeline:
    async def recall(
        self, session: AsyncSession, query: str, *, top_k: int = 8,
    ) -> tuple[list[dict], dict]:
        terms = extract_query_terms(query)
        t_start = time.perf_counter()

        # ---- Stage 0 + 四路召回（每路独立降级）----
        vector: list[dict] = []
        used_vec = False
        try:
            vector = await vector_recall(session, query, top_k=settings.top_k_recall)
            used_vec = bool(vector)
        except Exception:
            vector = []

        lexical: list[dict] = []
        used_es = False
        try:
            lexical = await bm25_recall(query, top_k=settings.top_k_recall)
            used_es = bool(lexical)
        except Exception:
            lexical = []
        if not lexical:  # ES BM25 不可用 → PG 词法召回降级
            lexical = await lexical_recall(session, terms, top_k=settings.top_k_recall)

        seed_ids = [
            r["chunk_id"] for r in (vector + lexical) if r.get("kind") == "code"
        ][:_SEED_TOP]
        graph = await graph_recall(
            session, seed_ids, depth=_SEED_DEPTH, max_nodes=_GRAPH_MAX_NODES
        )

        # ---- Stage 1: RRF 融合 ----
        fused = rrf_fuse(
            {"vector": vector, "lexical": lexical, "graph": graph},
            weights=DEFAULT_WEIGHTS,
            k=settings.rrf_k,
        )
        pool = fused[: settings.rerank_pool]
        recall_ms = int((time.perf_counter() - t_start) * 1000)

        # ---- Stage 2/3: 精排（每阶段独立降级；任一失败沿用当前候选集）----
        rerank_on = False
        coarse_n: int | None = None
        candidates = pool
        t_rerank = time.perf_counter()
        if reranker_client.enabled():
            if settings.reranker_coarse_model:
                try:
                    candidates = await rerank_stage(
                        query, candidates,
                        model=settings.reranker_coarse_model,
                        top_n=settings.top_k_coarse,
                    )
                    coarse_n = len(candidates)
                    rerank_on = True
                except Exception as e:  # 粗排失败：沿用 RRF 候选继续精排
                    logger.warning("coarse rerank failed ({}): {}", settings.reranker_coarse_model, e)
            if settings.reranker_fine_model:
                try:
                    candidates = await rerank_stage(
                        query, candidates,
                        model=settings.reranker_fine_model,
                        top_n=top_k,
                    )
                    rerank_on = True
                except Exception as e:  # 精排失败：若粗排也未生效则退回 RRF 排序
                    logger.warning("fine rerank failed ({}): {}", settings.reranker_fine_model, e)
        rerank_ms = int((time.perf_counter() - t_rerank) * 1000)
        candidates = candidates[:top_k]

        meta = {
            # 检索漏斗（前端检索详情 / 设计 §11 全景）
            "terms": terms,
            "recall": {"vector": len(vector), "lexical": len(lexical), "graph": len(graph)},
            "bm25": used_es,
            "vector_on": used_vec,
            "rrf_pool": len(fused),
            "coarse": coarse_n,
            "fine": len(candidates),
            "rerank_on": rerank_on,
            "recall_ms": recall_ms,
            "rerank_ms": rerank_ms,
            # 兼容旧字段（chat_service 降级提示 / 旧前端 RetrievalInfo）
            "lexical": len(lexical),
            "vector": len(vector),
            "graph": len(graph),
            "merged": len(fused),
        }
        return candidates, meta


pipeline = RetrievalPipeline()
