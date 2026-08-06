"""检索节点：复用 pipeline.recall（3 路召回 → RRF → 精排）。

接收 query_analysis 透传的 Stage 0（semantic_query/keywords/rewritten），避免 recall 内部
重复改写；精排后补 content_type 并构造引用。通过 get_stream_writer 推 retrieval / citation
SSE 事件（与 legacy stream_chat 同构）。
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agent.state import AgentState
from app.retrieval.pipeline import pipeline
from app.services.chat_service import _citation, _enrich_content_types


async def retrieve(state: AgentState, config: RunnableConfig) -> dict:
    session = config["configurable"]["session"]
    top_k = config["configurable"]["top_k"]
    query = state["query"]
    sem = state.get("semantic_query") or query
    terms = state.get("keywords") or []
    rewritten = state.get("rewritten")

    ranked, meta = await pipeline.recall(
        session, query, top_k=top_k,
        semantic_query=sem, terms=terms, rewritten=rewritten,
    )
    await _enrich_content_types(session, ranked)
    citations = [_citation(r) for r in ranked]

    writer = get_stream_writer()
    writer({"event": "retrieval", "data": meta})
    for cit in citations:
        writer({"event": "citation", "data": cit})

    return {"ranked": ranked, "retrieval_meta": meta, "citations": citations}
