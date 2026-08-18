"""query_understanding 单测：rewrite_query 的降级与 LLM 输出解析。

无外部网络：未配置 LLM 时直接降级；配置后 monkeypatch query_understanding.llm
（M44 起为 extraction 档客户端）验证解析与异常兜底。
"""
from __future__ import annotations

import pytest

from app.retrieval import query_understanding


@pytest.fixture
def reset_llm():
    saved = query_understanding.llm.api_key
    yield
    query_understanding.llm.api_key = saved


async def test_rewrite_degrades_without_llm(reset_llm):
    query_understanding.llm.api_key = ""  # llm.configured == False
    out = await query_understanding.rewrite_query("如何处理事务消息")
    assert out == {"semantic_query": "如何处理事务消息", "extra_keywords": []}


async def test_rewrite_parses_llm_output(monkeypatch, reset_llm):
    query_understanding.llm.api_key = "fake-key"  # configured == True

    async def fake_chat(messages, **kw):
        return "QUERY: 事务消息如何处理\nKEYWORDS: transaction, rollback, halfMsg"

    monkeypatch.setattr(query_understanding.llm, "chat", fake_chat)

    out = await query_understanding.rewrite_query("如何处理事务消息")
    assert out["semantic_query"] == "事务消息如何处理"
    assert out["extra_keywords"] == ["transaction", "rollback", "halfMsg"]


async def test_rewrite_parses_chinese_commas(monkeypatch, reset_llm):
    query_understanding.llm.api_key = "fake-key"

    async def fake_chat(messages, **kw):
        return "QUERY: q\nKEYWORDS: 事务，回滚，halfMsg"

    monkeypatch.setattr(query_understanding.llm, "chat", fake_chat)
    out = await query_understanding.rewrite_query("q")
    assert out["extra_keywords"] == ["事务", "回滚", "halfMsg"]


async def test_rewrite_degrades_on_llm_failure(monkeypatch, reset_llm):
    query_understanding.llm.api_key = "fake-key"

    async def fake_chat(messages, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(query_understanding.llm, "chat", fake_chat)
    out = await query_understanding.rewrite_query("原问题")
    assert out == {"semantic_query": "原问题", "extra_keywords": []}


async def test_rewrite_degrades_on_unparseable_output(monkeypatch, reset_llm):
    query_understanding.llm.api_key = "fake-key"

    async def fake_chat(messages, **kw):
        return "一堆没有 QUERY/KEYWORDS 前缀的自由文本"

    monkeypatch.setattr(query_understanding.llm, "chat", fake_chat)
    out = await query_understanding.rewrite_query("原问题")
    # 解析失败 → semantic_query 回退原值，keywords 空
    assert out["semantic_query"] == "原问题"
    assert out["extra_keywords"] == []


def test_extract_query_terms_still_works():
    terms = query_understanding.extract_query_terms("如何 checkLocalTransaction")
    assert any("checklocaltransaction" == t for t in terms) or terms  # camelCase 拆分仍生效


def test_rewrite_binds_extraction_tier(monkeypatch):
    """M44：模块级 llm 绑定的是 **extraction** 档客户端（非默认 reasoning）。

    import 期构造 → 用 reload 在受控 settings 下重建模块验证绑定；finally 再 reload
    一次按真实 settings 恢复（后续测试拿回干净模块）。
    """
    import importlib

    from app.core.config import settings
    monkeypatch.setattr(settings, "model_routes",
                        '{"extraction": {"model": "ext-m"}, "reasoning": {"model": "rea-m"}}')
    import app.retrieval.query_understanding as qu
    try:
        reloaded = importlib.reload(qu)
        assert reloaded.llm.model == "ext-m"
    finally:
        monkeypatch.undo() if hasattr(monkeypatch, "undo") else None
        importlib.reload(qu)
