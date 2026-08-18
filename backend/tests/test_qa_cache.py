"""M42 QA 缓存 legacy 路径测试（FakeRedis 注入，零真实 Redis/LLM）。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.chat_service as cs
from app.clients.cache_client import CacheClient, normalize_query, qa_cache_key
from app.core.config import settings


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}

    async def ping(self):
        return True

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, ex=None):
        self.store[k] = v


class _Conv:
    title = "t"
    agent_type = None
    target_repo = None


@pytest.fixture
def legacy_env(monkeypatch):
    box = {"rlog_meta": None, "assistant": None}
    async def fake_open(session, query, agent_type, conversation_id, target_repo=None):
        return _Conv(), "c1"
    async def fake_user(session, conv, q, at):
        return "m1"
    async def fake_persist(session, q, meta, cits, agent_steps=None):
        box["rlog_meta"] = meta
        return SimpleNamespace(log_id=1)
    async def fake_asst(session, conv, ans, cits, log_id, at, status=None):
        box["assistant"] = ans
        return "m2"
    monkeypatch.setattr(cs, "open_conversation", fake_open)
    monkeypatch.setattr(cs, "add_user_message", fake_user)
    monkeypatch.setattr(cs, "persist_retrieval_log", fake_persist)
    monkeypatch.setattr(cs, "add_assistant_message", fake_asst)
    monkeypatch.setattr(cs.pipeline, "recall",
                        AsyncMock(return_value=([], {"merged": 0})))
    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(cs, "_enrich_content_types", _noop)
    monkeypatch.setattr(cs, "load_conversation_history", AsyncMock(return_value=[]))
    import app.clients.cache_client as ccmod
    fake_cc = CacheClient(_FakeRedis())
    monkeypatch.setattr(ccmod, "get_cache_client", lambda: fake_cc)
    # configured 是只读 property，需在类级别 patch
    monkeypatch.setattr(type(cs.llm), "configured", True)
    return box, fake_cc


@pytest.mark.asyncio
async def test_qa_cache_hit_replays_and_skips_llm(monkeypatch, legacy_env):
    box, cc_ = legacy_env
    monkeypatch.setattr(settings, "rag_engine", "legacy")
    monkeypatch.setattr(settings, "domain_pack_default_repo", "repo-x")
    await cc_.qa_set(qa_cache_key("repo-x", normalize_query("Hello World")),
                     {"answer": "cached answer", "citations": [{"chunk_id": "c1"}],
                      "meta": {"merged": 3, "rewritten": False}})

    async def _must_not_stream(*a, **kw):
        raise AssertionError("命中缓存不得调 LLM")
    monkeypatch.setattr(cs.llm, "stream_tokens", _must_not_stream)

    events = [e async for e in cs.stream_chat(None, "  hello   WORLD ", conversation_id=None)]
    kinds = [e for e, _ in events]
    # SSE 契约：conversation → retrieval → citation* → token* → done
    assert kinds[0] == "conversation" and kinds[-1] == "done"
    assert "retrieval" in kinds and "token" in kinds
    assert box["rlog_meta"]["cache"] == "hit"
    assert box["assistant"] == "cached answer"
    toks = "".join(d["content"] for e, d in events if e == "token")
    assert toks == "cached answer"


@pytest.mark.asyncio
async def test_qa_cache_miss_then_store(monkeypatch, legacy_env):
    box, cc_ = legacy_env
    monkeypatch.setattr(settings, "rag_engine", "legacy")
    monkeypatch.setattr(settings, "domain_pack_default_repo", "repo-x")

    async def _fake_stream(messages, **kw):
        yield "gen-"
        yield "answer"

    monkeypatch.setattr(cs.llm, "stream_tokens", _fake_stream)
    _ = [e async for e in cs.stream_chat(None, "brand new question", conversation_id=None)]
    assert box["rlog_meta"].get("cache") is None      # 未命中不标
    assert box["assistant"] == "gen-answer"
    # 生成完成后写入缓存
    stored = await cc_.qa_get(qa_cache_key("repo-x", normalize_query("brand new question")))
    assert stored is not None
    assert stored["answer"] == "gen-answer"


@pytest.mark.asyncio
async def test_qa_cache_not_stored_when_gen_aborted(monkeypatch, legacy_env):
    box, cc_ = legacy_env
    monkeypatch.setattr(settings, "rag_engine", "legacy")
    monkeypatch.setattr(settings, "domain_pack_default_repo", "repo-x")

    async def _fail_stream(messages, **kw):
        raise RuntimeError("boom")
        yield ""   # pragma: no cover

    monkeypatch.setattr(cs.llm, "stream_tokens", _fail_stream)
    _ = [e async for e in cs.stream_chat(None, "failing question", conversation_id=None)]
    stored = await cc_.qa_get(qa_cache_key("repo-x", normalize_query("failing question")))
    assert stored is None                              # 失败答案不入缓存
