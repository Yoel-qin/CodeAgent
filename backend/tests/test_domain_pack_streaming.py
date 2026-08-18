"""M37 streaming：stream_graph 把激活 pack name 注入图 state（active_pack_name）。"""
from __future__ import annotations

import pytest

from app.domain_packs.models import DomainPack, Manifest


@pytest.mark.asyncio
async def test_stream_graph_injects_active_pack_name(monkeypatch):
    """resolve_active_pack 返 pack → state['active_pack_name'] = pack.manifest.name。"""
    from app.agent import streaming as mod

    pack = DomainPack(manifest=Manifest(name="rocketmq", target_repo="apache/rocketmq"))
    captured_state = {}

    # mock open_conversation：返一个带 conversation_id 的 conv
    class _Conv:
        title = "t"
        agent_type = None
        conversation_id = "conv1"
        target_repo = None
    monkeypatch.setattr(mod, "open_conversation", lambda *a, **kw: _async((_Conv(), "conv1")))
    monkeypatch.setattr(mod, "add_user_message", lambda *a, **kw: _async("msg1"))
    monkeypatch.setattr(mod, "load_conversation_history", lambda *a, **kw: _async([]))
    monkeypatch.setattr(mod, "resolve_active_pack", lambda conv: pack)
    monkeypatch.setattr(mod, "_enforce_into_stream",
                        lambda *a, **kw: ("ans", []))
    monkeypatch.setattr(mod, "persist_retrieval_log", lambda *a, **kw: _async(_rlog()))
    monkeypatch.setattr(mod, "add_assistant_message", lambda *a, **kw: _async("msg2"))

    # mock get_graph().astream 捕获 state；mock aget_state（无 interrupt）
    class _FakeGraph:
        async def astream(self, state, config=None, stream_mode=None):
            captured_state.update(state)
            return
            yield  # 让它成为 async generator
        async def aget_state(self, config):
            return type("S", (), {"tasks": [], "interrupts": None})()

    monkeypatch.setattr(mod, "get_graph", lambda: _FakeGraph())

    [e async for e in mod.stream_graph(session=None, query="q")]
    assert captured_state["active_pack_name"] == "rocketmq"


@pytest.mark.asyncio
async def test_stream_graph_no_pack_yields_none(monkeypatch):
    """resolve_active_pack 返 None → state['active_pack_name'] = None。"""
    from app.agent import streaming as mod
    captured_state = {}

    class _Conv:
        title = "t"
        agent_type = None
        conversation_id = "conv1"
        target_repo = None
    monkeypatch.setattr(mod, "open_conversation", lambda *a, **kw: _async((_Conv(), "conv1")))
    monkeypatch.setattr(mod, "add_user_message", lambda *a, **kw: _async("msg1"))
    monkeypatch.setattr(mod, "load_conversation_history", lambda *a, **kw: _async([]))
    monkeypatch.setattr(mod, "resolve_active_pack", lambda conv: None)
    monkeypatch.setattr(mod, "_enforce_into_stream", lambda *a, **kw: ("ans", []))
    monkeypatch.setattr(mod, "persist_retrieval_log", lambda *a, **kw: _async(_rlog()))
    monkeypatch.setattr(mod, "add_assistant_message", lambda *a, **kw: _async("msg2"))

    class _FakeGraph:
        async def astream(self, state, config=None, stream_mode=None):
            captured_state.update(state)
            return
            yield
        async def aget_state(self, config):
            return type("S", (), {"tasks": [], "interrupts": None})()

    monkeypatch.setattr(mod, "get_graph", lambda: _FakeGraph())
    [e async for e in mod.stream_graph(session=None, query="q")]
    assert captured_state["active_pack_name"] is None


# ---- 测试 helper ----
async def _async(v):
    return v


class _RLog:
    log_id = 1


def _rlog():
    return _RLog()
