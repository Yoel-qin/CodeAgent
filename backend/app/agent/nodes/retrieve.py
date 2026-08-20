"""检索节点：复用 pipeline.recall（3 路召回 → RRF → 精排）。

接收 query_analysis 透传的 Stage 0（semantic_query/keywords/rewritten），避免 recall 内部
重复改写；精排后补 content_type 并构造引用。通过 get_stream_writer 推 retrieval / citation
SSE 事件（与 legacy stream_chat 同构）。
M41：config 含 trace 时记 retrieval span（attrs 从 meta 取）。
"""
from __future__ import annotations

import time

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agent.state import AgentState
from app.retrieval.pipeline import pipeline
from app.services.chat_service import _citation, _enrich_content_types


async def retrieve(state: AgentState, config: RunnableConfig) -> dict:
    query = state["query"]
    # M42 QA 缓存（opt-in）：命中 → 跳过 recall，直接回放（token 已在此发，generate 短路）
    from app.clients.cache_client import get_cache_client, normalize_query, qa_cache_key
    cc = get_cache_client()
    qa_ctx = config["configurable"].get("qa_cache")
    if cc is not None and qa_ctx is not None:
        cached = await cc.qa_get(qa_cache_key(state.get("repo_key") or "",
                                              normalize_query(query)))
        if cached is not None:
            hit_meta = dict(cached.get("meta") or {})
            hit_meta["cache"] = "hit"
            hit_citations = list(cached.get("citations") or [])
            writer = get_stream_writer()
            writer({"event": "retrieval", "data": hit_meta})
            for cit in hit_citations:
                writer({"event": "citation", "data": cit})
            answer_text = cached.get("answer") or ""
            for i in range(0, len(answer_text), 64):
                writer({"event": "token", "data": {"content": answer_text[i:i + 64]}})
            qa_ctx["hit"] = True
            qa_ctx["answer"] = answer_text
            return {"ranked": [], "retrieval_meta": hit_meta,
                    "citations": hit_citations}
    session = config["configurable"]["session"]
    top_k = config["configurable"]["top_k"]
    allowed_kinds = config["configurable"].get("allowed_kinds")   # M45
    sem = state.get("semantic_query") or query
    terms = state.get("keywords") or []
    rewritten = state.get("rewritten")
    collector = config["configurable"].get("trace")

    t0 = time.perf_counter()
    ranked, meta = await pipeline.recall(
        session, query, top_k=top_k,
        semantic_query=sem, terms=terms, rewritten=rewritten,
        allowed_kinds=allowed_kinds,
    )
    if collector is not None:
        collector.record("retrieval", "recall",
                         (time.perf_counter() - t0) * 1000,
                         parent_id=collector.stack_top,
                         attrs={"recall": meta.get("recall"),
                                "merged": meta.get("merged"),
                                "rerank_on": meta.get("rerank_on"),
                                "rewritten": meta.get("rewritten", rewritten)})
    await _enrich_content_types(session, ranked)
    citations = [_citation(r) for r in ranked]

    writer = get_stream_writer()
    writer({"event": "retrieval", "data": meta})
    for cit in citations:
        writer({"event": "citation", "data": cit})

    return {"ranked": ranked, "retrieval_meta": meta, "citations": citations}
