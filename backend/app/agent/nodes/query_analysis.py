"""Stage 0 查询理解节点（复用 retrieval/query_understanding）+ 意图分类。

LLM 改写（失败优雅降级）+ 规则分词 + camelCase 拆分 → semantic_query/keywords/rewritten；
并做意图分类 + 协作判定（with_structured_output，失败→规则兜底）→ intent / needs_collab，
供 router 条件路由。改写与分类并行（各自独立降级，互不阻塞）。

M37：意图分类读激活领域包（state.active_pack_name）——有包时用领域版 system prompt
（含 trace/diagnose/tune 判定），无包时逐字同现状（不产领域 intent）。
"""
from __future__ import annotations

import asyncio

from app.agent.agents._domain_prompt import _pack_from_state
from app.agent.llm import classify_intent_and_collab
from app.agent.state import AgentState
from app.core.config import settings
from app.retrieval.query_understanding import extract_query_terms, rewrite_query


async def query_analysis(state: AgentState) -> dict:
    query = state["query"]
    pack = _pack_from_state(state)                       # M37：有包才启用领域 intent 分类
    rw, analyzed = await asyncio.gather(
        rewrite_query(query), classify_intent_and_collab(query, pack=pack))
    sem = rw["semantic_query"]
    terms = extract_query_terms(query)
    if rw["extra_keywords"]:
        seen = {t.lower() for t in terms}
        for k in rw["extra_keywords"]:
            kl = k.lower()
            if kl not in seen:
                terms.append(k)
                seen.add(kl)
    # needs_collab：LLM/规则判定 ∧ 开关（off → 强制 False = 零行为变更）
    needs_collab = bool(analyzed.needs_collab and settings.multi_agent_collab_enabled)
    return {
        "semantic_query": sem,
        "keywords": terms,
        "rewritten": sem != query or bool(rw["extra_keywords"]),
        "intent": analyzed.intent,
        "needs_collab": needs_collab,
    }
