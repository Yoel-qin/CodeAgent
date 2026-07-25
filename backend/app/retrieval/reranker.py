"""精排阶段（设计 §11.5 粗排 / §11.6 精排）。

两阶段统一封装为 :func:`rerank_stage`：给定 (query, candidates, model, top_n)，
调 ``clients.reranker_client`` 拿每条候选的相关性分数，按分重排并截断。
图特征融合（§11.7，α·semantic + β·graph + γ·structural）依赖 Phase 5 的
graph_embedding / pagerank / 社区等特征，**当前 Stage 3 为纯语义精排**，待 Phase 5 加层。
"""
from __future__ import annotations

from app.clients import reranker_client


async def rerank_stage(
    query: str, candidates: list[dict], *, model: str, top_n: int,
) -> list[dict]:
    """用 ``model`` 对 ``candidates`` 重排，返回按相关性降序、长度 ≤ top_n 的列表。

    每条候选的 ``content`` 作为文档文本送入 reranker；返回结果保留原候选全部元数据，
    仅把 ``score`` 覆盖为相关性分数。调用失败时抛异常，由管道捕获后降级到 RRF 排序。
    """
    if not candidates or not model:
        return candidates
    documents = [c.get("content") or "" for c in candidates]
    scored = await reranker_client.rerank(query, documents, model=model, top_n=top_n)
    return [{**candidates[idx], "score": float(score)} for idx, score in scored]
