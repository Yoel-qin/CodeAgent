"""Stage 0 查询理解节点（复用 retrieval/query_understanding）+ 意图分类。

LLM 改写（失败优雅降级）+ 规则分词 + camelCase 拆分 → semantic_query/keywords/rewritten；
并做意图分类 + 协作判定（with_structured_output，失败→规则兜底）→ intent / needs_collab，
供 router 条件路由。改写与分类并行（各自独立降级，互不阻塞）。

M37：意图分类读激活领域包（state.active_pack_name）——有包时用领域版 system prompt
（含 trace/diagnose/tune 判定），无包时逐字同现状（不产领域 intent）。
M41：config 含 trace 时记 intent/llm span；无 trace → 零开销直通。
"""
from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.agent.agents._domain_prompt import _pack_from_state
from app.agent.llm import classify_intent_and_collab
from app.agent.state import AgentState
from app.agent.trace import SpanCollector, tokens_from_usage
from app.core.config import settings
from app.retrieval.query_understanding import extract_query_terms, rewrite_query


async def query_analysis(state: AgentState, config: RunnableConfig) -> dict:
    query = state["query"]
    pack = _pack_from_state(state)                       # M37：有包才启用领域 intent 分类
    collector: SpanCollector | None = config["configurable"].get("trace")

    async def _rewrite() -> dict:
        if collector is None:
            return await rewrite_query(query)
        span = collector.start("llm", "rewrite", parent_id=collector.stack_top)
        u: dict = {}
        try:
            rw = await rewrite_query(query, usage_out=u)
        except Exception:
            collector.end(span, error="rewrite failed")
            raise
        collector.end(span, tokens=tokens_from_usage(
            u or None, prompt_chars=len(query),
            completion_chars=len(rw.get("semantic_query", ""))))
        return rw

    if collector is not None:
        isp = collector.start("intent", "query_analysis")
        rw, analyzed = await asyncio.gather(
            _rewrite(),
            classify_intent_and_collab(query, pack=pack, collector=collector))
        needs_collab = bool(analyzed.needs_collab and settings.multi_agent_collab_enabled)
        isp.attrs.update({"intent": analyzed.intent, "needs_collab": needs_collab})
        collector.end(isp)
    else:
        rw, analyzed = await asyncio.gather(
            rewrite_query(query), classify_intent_and_collab(query, pack=pack))
        needs_collab = bool(analyzed.needs_collab and settings.multi_agent_collab_enabled)

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
        "intent": analyzed.intent,
        "needs_collab": needs_collab,
    }
