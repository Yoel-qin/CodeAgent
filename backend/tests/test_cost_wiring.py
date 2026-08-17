"""M42 双引擎接线测试：configurable["cost"] 注入 + meta.cost 回写 + 生成前 check。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.chat_service as cs
from app.clients.llm_client import LLMClient
from app.core.config import settings


class _Conv:
    title = "t"
    agent_type = None
    target_repo = None


async def _patch_legacy_persist(monkeypatch, rlog_box):
    async def fake_open(session, query, agent_type, conversation_id, target_repo=None):
        return _Conv(), "c1"
    async def fake_user(session, conv, q, at):
        return "m1"
    async def fake_persist(session, q, meta, cits, agent_steps=None):
        rlog = SimpleNamespace(log_id=1, recall_results=meta)
        rlog_box.append(rlog)
        return rlog
    async def fake_asst(session, conv, ans, cits, log_id, at, status=None):
        return "m2"
    monkeypatch.setattr(cs, "open_conversation", fake_open)
    monkeypatch.setattr(cs, "add_user_message", fake_user)
    monkeypatch.setattr(cs, "persist_retrieval_log", fake_persist)
    monkeypatch.setattr(cs, "add_assistant_message", fake_asst)


@pytest.mark.asyncio
async def test_legacy_cost_meta_written(monkeypatch):
    rlog_box: list = []
    await _patch_legacy_persist(monkeypatch, rlog_box)
    monkeypatch.setattr(settings, "cost_control_enabled", True)
    monkeypatch.setattr(cs.pipeline, "recall",
                        AsyncMock(return_value=([], {"merged": 0})))
    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(cs, "_enrich_content_types", _noop)
    monkeypatch.setattr(LLMClient, "configured", property(lambda self: False))   # 无 key：走 notice 路径
    monkeypatch.setattr(cs, "_no_key_notice", lambda meta: "no-key notice")
    events = [e async for e in cs.stream_chat(None, "q", conversation_id=None)]
    # rlog 回写后 recall_results 带 cost（enabled/spent=0）
    assert rlog_box[0].recall_results["cost"]["enabled"] is True
    assert rlog_box[0].recall_results["cost"]["exceeded"] is None
    assert [e for e in events if e[0] == "done"]


@pytest.mark.asyncio
async def test_legacy_usage_recorded_and_written_back(monkeypatch):
    """流式 usage 真值记量 → 超 token 闸置位 → cost 随 recall_results 回写；请求不炸。"""
    rlog_box: list = []
    await _patch_legacy_persist(monkeypatch, rlog_box)
    monkeypatch.setattr(settings, "cost_control_enabled", True)
    monkeypatch.setattr(settings, "cost_max_tokens_per_request", 10)
    monkeypatch.setattr(cs.pipeline, "recall",
                        AsyncMock(return_value=([], {"merged": 0})))
    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(cs, "_enrich_content_types", _noop)
    monkeypatch.setattr(LLMClient, "configured", property(lambda self: True))
    async def _hist(session, cid, exclude_message_id=None, limit=6):
        return []
    monkeypatch.setattr(cs, "load_conversation_history", _hist)

    async def _fake_stream(messages, **kw):
        uo = kw.get("usage_out")
        if uo is not None:
            uo.update({"prompt_tokens": 999, "completion_tokens": 1})
        yield "hi"

    monkeypatch.setattr(cs.llm, "stream_tokens", _fake_stream)
    events = [e async for e in cs.stream_chat(None, "q", conversation_id=None)]
    assert [e for e in events if e[0] == "done"]              # 请求不炸
    meta = rlog_box[0].recall_results                          # 回写后的重赋值 dict
    assert meta["cost"]["exceeded"] == "tokens"
    assert meta["cost"]["spent_tokens"] == 1000
    assert meta["cost"]["estimated"] is False


@pytest.mark.asyncio
async def test_stream_graph_injects_cost(monkeypatch):
    import app.agent.streaming as sg
    captured: dict = {}

    class _FakeGraph:
        async def astream(self, state, config=None, stream_mode=None):
            captured["config"] = config
            yield {"event": "token", "data": {"content": "hi"}}
            yield {"event": "retrieval", "data": {"merged": 0}}

        async def aget_state(self, config):
            return SimpleNamespace(tasks=[])

    # sg 从 chat_service import 的 helper 在 sg 命名空间——必须打在 sg 上
    monkeypatch.setattr(sg, "get_graph", lambda: _FakeGraph())
    monkeypatch.setattr(settings, "cost_control_enabled", True)
    rlog_box: list = []

    async def fake_open(session, query, agent_type, conversation_id, target_repo=None):
        return _Conv(), "c1"
    async def fake_user(session, conv, q, at):
        return "m1"
    async def fake_persist(session, q, meta, cits, agent_steps=None):
        rlog = SimpleNamespace(log_id=1, recall_results=meta)
        rlog_box.append(rlog)
        return rlog
    async def fake_asst(session, conv, ans, cits, log_id, at, status=None):
        return "m2"
    async def _hist(session, cid, exclude_message_id=None, limit=6):
        return []

    monkeypatch.setattr(sg, "open_conversation", fake_open)
    monkeypatch.setattr(sg, "add_user_message", fake_user)
    monkeypatch.setattr(sg, "persist_retrieval_log", fake_persist)
    monkeypatch.setattr(sg, "add_assistant_message", fake_asst)
    monkeypatch.setattr(sg, "load_conversation_history", _hist)
    events = [e async for e in sg.stream_graph(None, "q", conversation_id=None)]
    assert captured["config"]["configurable"]["cost"] is not None
    assert rlog_box[0].recall_results["cost"]["enabled"] is True
    assert [e for e in events if e[0] == "done"]
