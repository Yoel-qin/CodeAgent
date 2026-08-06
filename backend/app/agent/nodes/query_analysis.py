"""Stage 0 查询理解节点（复用 retrieval/query_understanding）+ 意图分类。

LLM 改写（失败优雅降级）+ 规则分词 + camelCase 拆分 → semantic_query/keywords/rewritten；
并做意图分类（with_structured_output，失败→规则兜底）→ intent，供 router 条件路由。
改写与分类并行（各自独立降级，互不阻塞）。
"""
from __future__ import annotations

import asyncio

from app.agent.llm import classify_intent
from app.agent.state import AgentState
from app.retrieval.query_understanding import extract_query_terms, rewrite_query


async def query_analysis(state: AgentState) -> dict:
    query = state["query"]
    rw, intent = await asyncio.gather(rewrite_query(query), classify_intent(query))
    sem = rw["semantic_query"]
    terms = extract_query_terms(query)
    if rw["extra_keywords"]:
        seen = {t.lower() for t in terms}
        for k in rw["extra_keywords"]:
            kl = k.lower()
            if kl not in seen:
                terms.append(k)
                seen.add(kl)
    return {
        "semantic_query": sem,
        "keywords": terms,
        "rewritten": sem != query or bool(rw["extra_keywords"]),
        "intent": intent,
    }
