"""M37 领域 intent：规则兜底 pack_active 门控 + classify 的 system prompt 变体选择 + schema 分离。"""
from __future__ import annotations

from app.agent import llm
from app.agent.llm import (
    _INTENT_SYS,
    _INTENT_SYS_DOMAIN,
    _IntentSchemaBase,
    _rule_intent,
    classify_intent_and_collab,
)
from app.domain_packs.models import DomainPack, Manifest


def _pack() -> DomainPack:
    return DomainPack(manifest=Manifest(name="rocketmq", target_repo="apache/rocketmq"))


def test_rule_intent_no_pack_does_not_yield_domain():
    # "消息堆积" 命中 _DIAGNOSE_HINTS，但 pack_active=False 应门控掉
    assert _rule_intent("消息堆积排查", pack_active=False) != "diagnose"
    assert _rule_intent("发送链路", pack_active=False) != "trace"
    assert _rule_intent("提高吞吐", pack_active=False) != "tune"


def test_rule_intent_with_pack_yields_domain():
    assert _rule_intent("消息堆积", pack_active=True) == "diagnose"
    assert _rule_intent("发送链路", pack_active=True) == "trace"
    assert _rule_intent("提高吞吐", pack_active=True) == "tune"


def test_rule_intent_default_pack_active_false_backcompat():
    # 旧调用（不传 pack_active）逐字同现状
    assert _rule_intent("这段代码怎么用") == _rule_intent("这段代码怎么用", pack_active=False)


def test_classify_no_pack_uses_base_sys(monkeypatch):
    captured = {}

    class _FakeStructured:
        def __init__(self, schema):
            self.schema = schema

        async def ainvoke(self, messages, **kwargs):
            captured["sys"] = messages[0]["content"]
            return llm.IntentSchema(intent="code", needs_collab=False)

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "get_chat_model", lambda: type("M", (), {
        "with_structured_output": lambda self, schema: _FakeStructured(schema)})())
    import asyncio
    asyncio.run(classify_intent_and_collab("any", pack=None))
    assert captured["sys"] == _INTENT_SYS
    assert "领域意图" not in captured["sys"]


def test_classify_with_pack_uses_domain_sys(monkeypatch):
    captured = {}

    class _FakeStructured:
        def __init__(self, schema):
            self.schema = schema

        async def ainvoke(self, messages, **kwargs):
            captured["sys"] = messages[0]["content"]
            return llm.IntentSchema(intent="trace", needs_collab=False)

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "get_chat_model", lambda: type("M", (), {
        "with_structured_output": lambda self, schema: _FakeStructured(schema)})())
    import asyncio
    asyncio.run(classify_intent_and_collab("any", pack=_pack()))
    assert captured["sys"] == _INTENT_SYS_DOMAIN
    assert "领域意图" in captured["sys"]


def test_classify_no_pack_uses_base_schema(monkeypatch):
    """pack=None 时 with_structured_output 收到 _IntentSchemaBase（9 标签），非 IntentSchema（12 标签）。"""
    captured_schema = {}

    class _FakeStructured:
        def __init__(self, schema):
            captured_schema["v"] = schema

        async def ainvoke(self, messages):
            return llm.IntentSchema(intent="code", needs_collab=False)

    class _FakeModel:
        def with_structured_output(self, schema):
            return _FakeStructured(schema)

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "get_chat_model", lambda: _FakeModel())
    import asyncio
    asyncio.run(classify_intent_and_collab("any", pack=None))
    # 无包必须用 _IntentSchemaBase（9 标签），排除领域标签
    assert captured_schema["v"] is _IntentSchemaBase


def test_classify_with_pack_uses_full_schema(monkeypatch):
    """pack 激活时 with_structured_output 收到 IntentSchema（12 标签）。"""
    captured_schema = {}

    class _FakeStructured:
        def __init__(self, schema):
            captured_schema["v"] = schema

        async def ainvoke(self, messages):
            return llm.IntentSchema(intent="trace", needs_collab=False)

    class _FakeModel:
        def with_structured_output(self, schema):
            return _FakeStructured(schema)

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "get_chat_model", lambda: _FakeModel())
    import asyncio
    asyncio.run(classify_intent_and_collab("any", pack=_pack()))
    # 有包必须用完整 IntentSchema（12 标签）
    assert captured_schema["v"] is llm.IntentSchema
