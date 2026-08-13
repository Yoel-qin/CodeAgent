"""Agent 地基集成测：build_graph 编译 + 跑通 astream(stream_mode="custom")，
断言事件序列与 legacy stream_chat 同构（retrieval → citation(s) → token(s)），且引用/检索形状正确。

全程 mock 检索（pipeline.recall）与 LLM（llm.stream_tokens），无需 DB/向量库/ES/网络。
"""
from __future__ import annotations

import app.clients.llm_client as llm_mod
from app.agent.graph import build_graph
from app.agent.llm import IntentSchema


async def _wire_graph_mocks(monkeypatch):
    # Stage 0：避免任何真实 LLM 网络调用
    async def fake_rw(q):
        return {"semantic_query": q, "extra_keywords": []}

    monkeypatch.setattr("app.agent.nodes.query_analysis.rewrite_query", fake_rw)
    # 意图分类：返回 chitchat（无对应场景 Agent），使 router 走 retrieve→generate 兜底支路（agent 支路另测）
    async def fake_classify(q, *, pack=None):
        return IntentSchema(intent="chitchat", needs_collab=False)

    monkeypatch.setattr("app.agent.nodes.query_analysis.classify_intent_and_collab", fake_classify)

    # 检索：固定两路候选（code + doc）
    candidates = [
        {"chunk_id": "c1", "kind": "code", "content": "src", "class_name": "A",
         "method_name": "m1", "score": 0.9, "heading_path": None},
        {"chunk_id": "d1", "kind": "doc", "content": "doc", "class_name": None,
         "method_name": None, "score": 0.8, "heading_path": ["Sec"]},
    ]
    meta = {
        "terms": ["a"], "recall": {"vector": 2, "lexical": 0, "graph": 0},
        "merged": 2, "coarse": None, "fine": 2, "rerank_on": False,
        "recall_ms": 1, "rerank_ms": 0, "rewritten": False,
        "vector": 2, "lexical": 0, "graph": 0, "bm25": False,
        "vector_on": True, "rrf_pool": 2, "embedding_strategy": "unified",
    }

    async def fake_recall(session, query, **kw):
        return (candidates, meta)

    monkeypatch.setattr("app.retrieval.pipeline.pipeline.recall", fake_recall)

    async def noop(*a, **k):
        return None

    monkeypatch.setattr("app.agent.nodes.retrieve._enrich_content_types", noop)

    # LLM：固定 token 流
    monkeypatch.setattr(llm_mod.LLMClient, "configured", property(lambda self: True))

    async def fake_stream(messages):
        for t in ("An", "sw", "er"):
            yield t

    monkeypatch.setattr(llm_mod.llm, "stream_tokens", fake_stream)
    return meta


async def test_graph_compiles():
    graph = build_graph()
    assert graph is not None


async def test_graph_event_sequence(monkeypatch):
    await _wire_graph_mocks(monkeypatch)
    graph = build_graph()

    state = {"query": "A.m1 做了什么", "conversation_id": "conv_test", "agent_type": None}
    config = {"configurable": {"thread_id": "conv_test", "session": None,
                               "top_k": 8, "agent_type": None}}

    events: list[dict] = []
    async for chunk in graph.astream(state, config=config, stream_mode="custom"):
        events.append(chunk)

    seq = [e["event"] for e in events]
    # 首个为 retrieval，紧跟 2 条 citation，再跟 token
    assert seq[0] == "retrieval"
    assert seq.count("citation") == 2
    cit_idx = [i for i, e in enumerate(events) if e["event"] == "citation"]
    tok_idx = [i for i, e in enumerate(events) if e["event"] == "token"]
    assert max(cit_idx) < min(tok_idx)
    # token 拼接 == "Answer"
    assert "".join(e["data"]["content"] for e in events if e["event"] == "token") == "Answer"

    # retrieval 形状
    retr = next(e["data"] for e in events if e["event"] == "retrieval")
    assert retr["merged"] == 2 and retr["recall"]["vector"] == 2

    # citation 形状（与 legacy _citation 同构）
    cit = next(e["data"] for e in events if e["event"] == "citation")
    assert {"type", "chunk_id", "label", "score", "content_type"}.issubset(cit.keys())
