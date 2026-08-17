"""Agent 地基单测：state reducer + 各节点 transform（mock 检索/LLM，无需 infra）。

asyncio_mode=auto（见 pyproject [tool.pytest]）→ async def 测试自动按 asyncio 运行。
"""
from __future__ import annotations

import app.clients.llm_client as llm_mod
from app.agent.nodes.generate import generate
from app.agent.nodes.query_analysis import query_analysis
from app.agent.nodes.retrieve import retrieve

# ---- state reducer：add_messages（追加 / 同 id 去重）----


def test_add_messages_appends_distinct():
    from langgraph.graph.message import add_messages

    out = add_messages([], [{"role": "user", "content": "hi"},
                            {"role": "assistant", "content": "hello"}])
    assert len(out) == 2


def test_add_messages_replaces_same_id():
    from langgraph.graph.message import add_messages

    m1 = {"id": "1", "role": "user", "content": "old"}
    m2 = {"id": "1", "role": "user", "content": "new"}
    out = add_messages([m1], m2)
    assert len(out) == 1  # 同 id 去重（保留最新）


# ---- query_analysis 节点 ----


async def test_query_analysis(monkeypatch):
    async def fake_rw(q):
        return {"semantic_query": q + " 重写", "extra_keywords": ["Foo"]}

    monkeypatch.setattr("app.agent.nodes.query_analysis.rewrite_query", fake_rw)

    out = await query_analysis({"query": "A.m1 做了什么"}, {"configurable": {}})
    assert out["semantic_query"] == "A.m1 做了什么 重写"
    assert "Foo" in out["keywords"]
    assert out["rewritten"] is True


# ---- retrieve 节点（mock pipeline.recall + _enrich_content_types）----


_CANDIDATES = [
    {"chunk_id": "c1", "kind": "code", "content": "src", "class_name": "A",
     "method_name": "m1", "score": 0.9, "heading_path": None},
]
_META = {
    "terms": ["a", "m1"], "recall": {"vector": 1, "lexical": 0, "graph": 0},
    "merged": 1, "coarse": None, "fine": 1, "rerank_on": False,
    "recall_ms": 1, "rerank_ms": 0, "rewritten": False,
    "vector": 1, "lexical": 0, "graph": 0,
}


async def test_retrieve(monkeypatch):
    pushed: list[dict] = []
    monkeypatch.setattr("app.agent.nodes.retrieve.get_stream_writer",
                        lambda: lambda d: pushed.append(d))

    async def fake_recall(session, query, **kw):
        return (_CANDIDATES, _META)

    monkeypatch.setattr("app.retrieval.pipeline.pipeline.recall", fake_recall)

    async def noop(*a, **k):
        return None

    monkeypatch.setattr("app.agent.nodes.retrieve._enrich_content_types", noop)

    out = await retrieve(
        {"query": "x", "semantic_query": "x", "keywords": ["x"], "rewritten": False},
        {"configurable": {"session": None, "top_k": 8}},
    )
    assert out["ranked"][0]["chunk_id"] == "c1"
    assert out["retrieval_meta"]["merged"] == 1
    assert len(out["citations"]) == 1
    assert [e["event"] for e in pushed] == ["retrieval", "citation"]
    assert pushed[0]["data"] is _META


# ---- generate 节点（configured → 逐 token；未配置 → 降级提示）----


async def test_generate_streams_tokens(monkeypatch):
    pushed: list[dict] = []
    monkeypatch.setattr("app.agent.nodes.generate.get_stream_writer",
                        lambda: lambda d: pushed.append(d))
    monkeypatch.setattr(llm_mod.LLMClient, "configured", property(lambda self: True))

    async def fake_stream(messages, *, usage_out=None):
        for t in ("Hel", "lo"):
            yield t

    monkeypatch.setattr(llm_mod.llm, "stream_tokens", fake_stream)

    out = await generate({"query": "hi", "ranked": [], "retrieval_meta": {}},
                         {"configurable": {"agent_type": None}})
    assert out["answer"] == "Hello"
    assert [e["event"] for e in pushed] == ["token", "token"]
    assert "".join(e["data"]["content"] for e in pushed) == "Hello"


async def test_generate_no_key_degrades(monkeypatch):
    pushed: list[dict] = []
    monkeypatch.setattr("app.agent.nodes.generate.get_stream_writer",
                        lambda: lambda d: pushed.append(d))
    monkeypatch.setattr(llm_mod.LLMClient, "configured", property(lambda self: False))

    out = await generate({"query": "hi", "ranked": [], "retrieval_meta": _META},
                         {"configurable": {"agent_type": None}})
    assert "未配置" in out["answer"]
    assert len(pushed) == 1 and pushed[0]["event"] == "token"
