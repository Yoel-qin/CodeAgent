"""检索管道编排（设计 §11）。

    Stage 0 查询理解（规则分词 + LLM 改写，可降级）
      → 三路召回（A 向量[unified 单路 / dual 代码CodeBERT+文档BGE-M3 双路] / B ES BM25（不可用降级 PG 词法）/ D 图遍历）
      → Stage 1 RRF 融合去重（§11.4）
      → Stage 2 粗排 bge-reranker-base（§11.5）
      → Stage 3 精排 bge-reranker-v2-m3（§11.6；dual 框架下作为统一重排桥，屏蔽两嵌入空间分数差异）

每路召回与整段精排均独立 try/except：向量/BM25 不可用退回词法召回，
精排不可用（无 Key / API 失败）退回 RRF 排序——主链路永不因可选项中断。
"""
from __future__ import annotations

import time

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import reranker_client
from app.core.config import settings
from app.retrieval.ablation import AblationConfig
from app.retrieval.bm25_search import bm25_recall
from app.retrieval.fusion import DEFAULT_WEIGHTS, rrf_fuse
from app.retrieval.graph_traverse import graph_recall
from app.retrieval.lexical_search import lexical_recall
from app.retrieval.query_understanding import extract_query_terms, rewrite_query
from app.retrieval.reranker import rerank_stage
from app.retrieval.vector_search import vector_recall

_SEED_DEPTH = 1
_GRAPH_MAX_NODES = 12
_SEED_TOP = 5  # 取代码召回前 N 条作为图遍历种子


class RetrievalPipeline:
    async def recall(
        self, session: AsyncSession, query: str, *, top_k: int = 8,
        semantic_query: str | None = None, terms: list[str] | None = None,
        rewritten: bool | None = None,
        ablation: AblationConfig | None = None,
        allowed_kinds: set[str] | None = None,
    ) -> tuple[list[dict], dict]:
        # ---- Stage 0：LLM 查询改写（失败优雅降级）----
        # 调用方可预计算 Stage 0（如 LangGraph 的 query_analysis 节点）后透传，避免重复改写；
        # 不传（legacy / 默认）则在此现算——行为与重构前完全一致。
        if semantic_query is None:
            rw = await rewrite_query(query)
            sem = rw["semantic_query"]
            terms = extract_query_terms(query)
            if rw["extra_keywords"]:
                seen_l = {t.lower() for t in terms}
                for k in rw["extra_keywords"]:
                    if k.lower() not in seen_l:
                        terms.append(k)
                        seen_l.add(k.lower())
            rewritten = sem != query or bool(rw["extra_keywords"])
        else:
            sem = semantic_query
            terms = terms or []
            if rewritten is None:
                rewritten = sem != query
        t_start = time.perf_counter()

        # ---- 三路召回（每路独立降级；ablation 可整路关闭，A/B 评测用）----
        # ab=None（生产）等价 full()：四路全开，行为与重构前完全一致。
        ab = ablation or AblationConfig()
        vector: list[dict] = []
        used_vec = False
        if ab.vector:
            try:
                vector = await vector_recall(session, sem, top_k=settings.top_k_recall,
                                                allowed_kinds=allowed_kinds)
                used_vec = bool(vector)
            except Exception:
                vector = []

        lexical: list[dict] = []
        used_es = False
        if ab.lexical:
            try:
                lexical = await bm25_recall(sem, top_k=settings.top_k_recall,
                                                  allowed_kinds=allowed_kinds)
                used_es = bool(lexical)
            except Exception:
                lexical = []
            if not lexical:  # ES BM25 不可用 → PG 词法召回降级
                lexical = await lexical_recall(session, terms, top_k=settings.top_k_recall,
                                                              allowed_kinds=allowed_kinds)

        seed_ids = [
            r["chunk_id"] for r in (vector + lexical) if r.get("kind") == "code"
        ][:_SEED_TOP]
        graph: list[dict] = []
        if ab.graph and (allowed_kinds is None or "code" in allowed_kinds):
            graph = await graph_recall(
                session, seed_ids, depth=_SEED_DEPTH, max_nodes=_GRAPH_MAX_NODES
            )

        # ---- Stage 1: RRF 融合 ----
        fused = rrf_fuse(
            {"vector": vector, "lexical": lexical, "graph": graph},
            weights=DEFAULT_WEIGHTS,
            k=settings.rrf_k,
        )
        # M45：RRF 后兜底过滤（防止下游 store 泄漏非允许 kind）
        if allowed_kinds is not None:
            fused = [c for c in fused if c.get("kind") in allowed_kinds]
        pool = fused[: settings.rerank_pool]
        recall_ms = int((time.perf_counter() - t_start) * 1000)

        # ---- Stage 2/3: 精排（每阶段独立降级；任一失败沿用当前候选集）----
        rerank_on = False
        coarse_n: int | None = None
        candidates = pool
        t_rerank = time.perf_counter()
        if ab.rerank and reranker_client.enabled():
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
        # M45：最终兜底过滤（精排不应改变 kind，但防御性兜底）
        if allowed_kinds is not None:
            candidates = [c for c in candidates if c.get("kind") in allowed_kinds]

        meta = {
            # 检索漏斗（前端检索详情 / 设计 §11 全景）
            "terms": terms,
            "recall": {"vector": len(vector), "lexical": len(lexical), "graph": len(graph)},
            # M25 诊断：三路候选的 slim 投影（chunk_id+kind），供评测逐 query 定位
            # 「向量路漏召 / 返回了什么 kind」（如 dual 向量路对中文 NL 返回 doc 漏 code）。生产不读，加性。
            "recall_paths": {
                "vector": [{"chunk_id": c.get("chunk_id"), "kind": c.get("kind")} for c in vector],
                "lexical": [{"chunk_id": c.get("chunk_id"), "kind": c.get("kind")} for c in lexical],
                "graph": [{"chunk_id": c.get("chunk_id"), "kind": c.get("kind")} for c in graph],
            },
            "bm25": used_es,
            "vector_on": used_vec,
            "rrf_pool": len(fused),
            "coarse": coarse_n,
            "fine": len(candidates),
            "rerank_on": rerank_on,
            "recall_ms": recall_ms,
            "rerank_ms": rerank_ms,
            "rewritten": rewritten,
            "embedding_strategy": settings.embedding_strategy,
            # 兼容旧字段（chat_service 降级提示 / 旧前端 RetrievalInfo）
            "lexical": len(lexical),
            "vector": len(vector),
            "graph": len(graph),
            "merged": len(fused),
        }
        return candidates, meta


pipeline = RetrievalPipeline()
