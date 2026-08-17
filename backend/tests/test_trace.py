"""M41 SpanCollector：树结构/异常重抛/估算/序列化/并发。"""
from __future__ import annotations

import asyncio

import pytest

from app.agent.trace import SpanCollector, llm_span, tokens_from_usage


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
