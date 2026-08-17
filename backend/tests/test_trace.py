"""M41 SpanCollector：树结构/异常重抛/估算/序列化/并发。"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.agent.llm import TraceCallbackHandler
from app.agent.nodes.generate import generate as generate_node
from app.agent.nodes.query_analysis import query_analysis
from app.agent.nodes.retrieve import retrieve as retrieve_node
from app.agent.trace import SpanCollector, llm_span, tokens_from_usage
from app.retrieval.query_understanding import rewrite_query


def test_nested_parents_and_duration():
    c = SpanCollector()
    with c.span("request", "chat") as root:
        with c.span("agent", "code_understand") as agent:
            assert agent.parent_id == root.span_id
    payload = c.to_payload()
    assert payload["version"] == 2
    assert payload["summary"]["n_spans"] == 2
    assert payload["summary"]["kind_counts"] == {"request": 1, "agent": 1}
    spans = payload["spans"]
    assert spans[0]["kind"] == "request" and spans[0]["parent_id"] is None
    assert spans[1]["parent_id"] == spans[0]["span_id"]
    assert spans[1]["duration_ms"] >= 0


def test_span_exception_recorded_and_reraised():
    c = SpanCollector()
    with pytest.raises(ValueError):
        with c.span("llm", "deepseek-chat"):
            raise ValueError("boom")
    s = c.to_payload()["spans"][0]
    assert s["status"] == "error"
    assert "ValueError" in s["error"]


def test_manual_start_end_for_parallel():
    c = SpanCollector()
    with c.span("intent", "query_analysis") as intent:
        a = c.start("llm", "rewrite", parent_id=intent.span_id)
        b = c.start("llm", "classify", parent_id=intent.span_id)
        c.end(a, tokens={"prompt": 10, "completion": 5, "estimated": False})
        c.end(b)
    spans = {s["name"]: s for s in c.to_payload()["spans"]}
    assert spans["rewrite"]["parent_id"] == intent.span_id
    assert spans["classify"]["parent_id"] == intent.span_id
    assert spans["rewrite"]["tokens"]["prompt"] == 10


def test_record_immediate_leaf():
    c = SpanCollector()
    with c.span("agent", "code_understand") as agent:
        c.record("tool", "search_code", 12.5, parent_id=agent.span_id,
                 attrs={"args": {"query": "q"}, "n": 3})
    s = [x for x in c.to_payload()["spans"] if x["kind"] == "tool"][0]
    assert s["duration_ms"] == 12.5 and s["attrs"]["n"] == 3
    assert s["parent_id"] == agent.span_id


def test_tokens_from_usage_real_vs_estimate():
    real = tokens_from_usage({"prompt_tokens": 100, "completion_tokens": 40})
    assert real == {"prompt": 100, "completion": 40, "estimated": False}
    est = tokens_from_usage(None, prompt_chars=400, completion_chars=88)
    assert est == {"prompt": 100, "completion": 22, "estimated": True}


def test_summary_tokens_aggregate_and_estimated_flag():
    c = SpanCollector()
    with c.span("request", "chat"):
        s1 = c.start("llm", "a", parent_id=1)
        c.end(s1, tokens={"prompt": 10, "completion": 5, "estimated": False})
        s2 = c.start("llm", "b", parent_id=1)
        c.end(s2, tokens={"prompt": 7, "completion": 3, "estimated": True})
    t = c.to_payload()["summary"]["tokens"]
    assert t == {"prompt": 17, "completion": 8, "n_llm_calls": 2, "estimated": True}


def test_empty_collector_payload():
    p = SpanCollector().to_payload()
    assert p["summary"]["n_spans"] == 0 and p["summary"]["total_ms"] == 0.0


async def test_llm_span_real_and_estimate():
    c = SpanCollector()
    async with llm_span(c, "deepseek-chat", prompt_text="x" * 400) as ls:
        ls.usage_out.update({"prompt_tokens": 11, "completion_tokens": 4})
    s1 = c.to_payload()["spans"][0]
    assert s1["tokens"] == {"prompt": 11, "completion": 4, "estimated": False}
    async with llm_span(c, "deepseek-chat") as ls2:
        ls2.add_token("y" * 80)
    s2 = c.to_payload()["spans"][1]
    assert s2["tokens"]["completion"] == 20 and s2["tokens"]["estimated"] is True


def test_gather_parallel_spans_independent():
    c = SpanCollector()

    async def slow():
        with c.span("llm", "slow"):
            await asyncio.sleep(0.03)

    async def fast():
        with c.span("llm", "fast"):
            await asyncio.sleep(0.005)

    async def main():
        await asyncio.gather(slow(), fast())

    asyncio.run(main())
    spans = {s["name"]: s for s in c.to_payload()["spans"]}
    assert spans["slow"]["duration_ms"] > spans["fast"]["duration_ms"]


# ---- Task 3 追加 ----


def _handler_spans(collector, *, usage=None):
    h = TraceCallbackHandler(collector)
    rid = uuid4()
    h.on_llm_start(serialized={}, prompts=[], run_id=rid)
    h.on_llm_new_token("abc", run_id=rid)
    resp = type("R", (), {"llm_output": {"token_usage": usage}} if usage else {"llm_output": None})()
    h.on_llm_end(resp, run_id=rid)
    return collector.to_payload()["spans"]


def test_trace_callback_handler_real_usage():
    c = SpanCollector()
    with c.span("agent", "code_understand"):
        spans = _handler_spans(c, usage={"prompt_tokens": 20, "completion_tokens": 7})
    s = spans[-1]
    assert s["kind"] == "llm" and s["parent_id"] is not None
    assert s["tokens"] == {"prompt": 20, "completion": 7, "estimated": False}


def test_trace_callback_handler_estimate_and_error():
    c = SpanCollector()
    spans = _handler_spans(c)  # 无 usage → 估算（prompt 记 0，completion 按 token chars）
    assert spans[-1]["tokens"]["estimated"] is True
    assert spans[-1]["tokens"]["completion"] == 0  # "abc" 3 chars // 4 == 0
    # error 路径：验证 duration 反映真实耗时且 status=error
    import time
    h = TraceCallbackHandler(c)
    rid = uuid4()
    h.on_llm_start(serialized={}, prompts=[], run_id=rid)
    time.sleep(0.05)
    h.on_llm_error(Exception("net"), run_id=rid)
    s = c.to_payload()["spans"][-1]
    assert s["status"] == "error"
    assert s["duration_ms"] >= 40  # 至少 ~50ms 的 sleep


def test_trace_callback_handler_never_raises():
    c = SpanCollector()
    TraceCallbackHandler(c).on_llm_end("garbage", run_id=uuid4())  # 非 LLMResult → 静默
    TraceCallbackHandler(None)  # collector=None → 可构造且回调全跳过


async def test_rewrite_query_usage_out_passthrough(monkeypatch):
    seen: dict = {}

    async def fake_chat_meta(messages, **kw):
        seen.update(kw)
        usage_out = kw.get("usage_out")
        if usage_out is not None:
            usage_out.update({"prompt_tokens": 5, "completion_tokens": 2})
        return "QUERY: consumer 拉取消息\nKEYWORDS: offset", {"prompt_tokens": 5,
                                                              "completion_tokens": 2}

    import app.retrieval.query_understanding as qu
    monkeypatch.setattr(qu.llm, "api_key", "fake-key")
    monkeypatch.setattr(qu.llm, "chat_meta", fake_chat_meta)
    out: dict = {}
    rw = await rewrite_query("consumer 怎么拉消息", usage_out=out)
    assert rw["semantic_query"] == "consumer 拉取消息"
    assert "usage_out" in seen and seen["usage_out"] is out
    assert out == {"prompt_tokens": 5, "completion_tokens": 2}


async def test_rewrite_query_without_usage_out_unchanged(monkeypatch):
    async def fake_chat(messages, **kw):
        return "QUERY: q2\nKEYWORDS:"

    import app.retrieval.query_understanding as qu
    monkeypatch.setattr(qu.llm, "api_key", "fake-key")
    monkeypatch.setattr(qu.llm, "chat", fake_chat)
    rw = await rewrite_query("q")
    assert rw == {"semantic_query": "q2", "extra_keywords": []}


# ---- Task 4 追加 ----


def _cfg(trace=None, session=None, top_k=8):
    return {"configurable": {"session": session, "top_k": top_k, "trace": trace}}


async def test_query_analysis_records_intent_and_llm_spans(monkeypatch):
    import app.agent.nodes.query_analysis as qa
    captured: dict = {}

    async def fake_rewrite(query, *, usage_out=None):
        captured["usage_out"] = usage_out
        return {"semantic_query": query, "extra_keywords": []}

    async def fake_classify(query, pack=None, *, collector=None):
        captured["collector"] = collector
        from app.agent.llm import IntentSchema
        return IntentSchema(intent="code", needs_collab=False)

    monkeypatch.setattr(qa, "rewrite_query", fake_rewrite)
    monkeypatch.setattr(qa, "classify_intent_and_collab", fake_classify)
    c = SpanCollector()
    state = await query_analysis({"query": "Broker 启动流程"}, _cfg(trace=c))
    assert state["intent"] == "code"
    spans = c.to_payload()["spans"]
    kinds = [s["kind"] for s in spans]
    assert "intent" in kinds and kinds.count("llm") >= 1
    assert captured["collector"] is c


async def test_query_analysis_without_trace_unchanged(monkeypatch):
    import app.agent.nodes.query_analysis as qa

    async def fake_rewrite(query, *, usage_out=None):
        return {"semantic_query": query, "extra_keywords": []}

    async def fake_classify(query, pack=None, *, collector=None):
        from app.agent.llm import IntentSchema
        return IntentSchema(intent="doc", needs_collab=False)

    monkeypatch.setattr(qa, "rewrite_query", fake_rewrite)
    monkeypatch.setattr(qa, "classify_intent_and_collab", fake_classify)
    state = await query_analysis({"query": "q"}, _cfg())  # 无 trace → 零开销直通
    assert state["intent"] == "doc"


async def test_retrieve_node_records_retrieval_span(monkeypatch):
    import app.agent.nodes.retrieve as rn

    class _Ranked(dict):
        pass

    async def fake_recall(session, query, top_k=8, **kw):
        return [{"chunk_id": "c1", "kind": "code", "score": 0.9,
                 "content": "src", "class_name": "A", "method_name": "m1"}], {
            "mode": "default", "merged": 1,
            "recall": {"vector": 1, "lexical": 0, "graph": 0}}

    async def fake_enrich(session, ranked):
        return None

    monkeypatch.setattr(rn.pipeline, "recall", fake_recall)
    monkeypatch.setattr(rn, "_enrich_content_types", fake_enrich)
    monkeypatch.setattr(rn, "get_stream_writer", lambda: (lambda chunk: None))
    c = SpanCollector()
    out = await retrieve_node({"query": "q", "semantic_query": "s", "keywords": [],
                               "rewritten": False}, _cfg(trace=c))
    assert out["ranked"]
    s = [x for x in c.to_payload()["spans"] if x["kind"] == "retrieval"][0]
    assert s["attrs"]["merged"] == 1


async def test_generate_node_records_llm_span(monkeypatch):
    import app.agent.nodes.generate as gn
    import app.clients.llm_client as llm_mod

    async def fake_stream(messages, *, usage_out=None, **kw):
        if usage_out is not None:
            usage_out.update({"prompt_tokens": 8, "completion_tokens": 6})
        for t in ("答", "案"):
            yield t

    monkeypatch.setattr(llm_mod.LLMClient, "configured", property(lambda self: True))
    monkeypatch.setattr(llm_mod.llm, "stream_tokens", fake_stream)
    monkeypatch.setattr(gn, "get_stream_writer", lambda: (lambda chunk: None))
    c = SpanCollector()
    await generate_node({"query": "q", "ranked": [], "retrieval_meta": {}}, _cfg(trace=c))
    s = [x for x in c.to_payload()["spans"] if x["kind"] == "llm"][0]
    assert s["tokens"] == {"prompt": 8, "completion": 6, "estimated": False}


async def test_run_scenario_agent_records_agent_span_and_degrade(monkeypatch):
    """agent span 包住嵌套 astream；异常 → degrade span，请求不中断。"""
    import app.clients.llm_client as llm_mod
    from app.agent.agents._base import run_scenario_agent

    class _BoomAgent:
        async def astream(self, *a, **kw):
            raise RuntimeError("agent boom")
            yield  # pragma: no cover

    monkeypatch.setattr(
        "app.agent.agents._base.configured", lambda: True)
    monkeypatch.setattr(
        "app.agent.agents._base.TokenSSEHandler",
        lambda *a, **kw: None)  # 防真实回调
    import app.agent.agents._base as base

    async def fake_recall(session, query, top_k=8, **kw):
        return [], {"mode": "default", "merged": 0}

    monkeypatch.setattr(base.pipeline, "recall", fake_recall)

    monkeypatch.setattr(llm_mod.LLMClient, "configured", property(lambda self: True))

    async def fake_stream_tokens(messages, *, usage_out=None, **kw):
        yield "降级答案"

    monkeypatch.setattr(llm_mod.llm, "stream_tokens", fake_stream_tokens)
    monkeypatch.setattr(base, "_safe_writer", lambda: (lambda chunk: None))
    monkeypatch.setattr(base, "_enrich_content_types", lambda s, r: None)

    c = SpanCollector()
    await run_scenario_agent(
        {"query": "q", "history": []}, _cfg(trace=c),
        agent_name="code_understand", tools=[],
        build_agent=_BoomAgent, degrade_label="代码理解",
    )
    kinds = [s["kind"] for s in c.to_payload()["spans"]]
    assert "agent" in kinds and "degrade" in kinds
    agent_span = [s for s in c.to_payload()["spans"] if s["kind"] == "agent"][0]
    assert agent_span["status"] == "error"
