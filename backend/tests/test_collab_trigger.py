"""M35 触发判定：classify_intent_and_collab / _rule_needs_collab / query_analysis / router 守卫。"""
from __future__ import annotations

import app.agent.llm as llm_mod
from app.agent.llm import _rule_needs_collab, classify_intent_and_collab
from app.agent.nodes import router as router_mod
from app.agent.nodes.query_analysis import query_analysis

# ---- _rule_needs_collab ----


def test_rule_needs_collab_mixed_intent():
    assert _rule_needs_collab("随便看看", "mixed") is True


def test_rule_needs_collab_diagnosis_signal():
    assert _rule_needs_collab("消费者消息堆积怎么排查", "bug") is True
    assert _rule_needs_collab("为什么这里死锁", "bug") is True


def test_rule_needs_collab_simple_code():
    assert _rule_needs_collab("getBalance 做了什么", "code") is False


# ---- classify_intent_and_collab：无 key 走规则 ----


async def test_classify_and_collab_no_key_uses_rule(monkeypatch):
    monkeypatch.setattr(llm_mod, "configured", lambda: False)
    r = await classify_intent_and_collab("消费者堆积排查")
    # "排查"是 _COLLAB_HINT 但非 _BUG_HINT → 规则意图回退 code；关键测 needs_collab
    assert r.intent == "code"
    assert r.needs_collab is True


async def test_classify_and_collab_llm_failure_falls_back(monkeypatch):
    monkeypatch.setattr(llm_mod, "configured", lambda: True)

    class _FakeStruct:
        async def ainvoke(self, msgs):
            raise RuntimeError("net down")

    def fake_model():
        class M:
            def with_structured_output(self, schema):
                return _FakeStruct()
        return M()

    monkeypatch.setattr(llm_mod, "get_chat_model", fake_model)
    r = await classify_intent_and_collab("getBalance 做了什么")
    assert r.intent == "code"
    assert r.needs_collab is False


# ---- query_analysis 产 needs_collab（开关 off → False）----


async def test_query_analysis_needs_collab_off_when_disabled(monkeypatch):
    async def fake_rw(q):
        return {"semantic_query": q, "extra_keywords": []}

    async def fake_classify(q, *, pack=None):
        return llm_mod.IntentSchema(intent="bug", needs_collab=True)

    monkeypatch.setattr("app.agent.nodes.query_analysis.rewrite_query", fake_rw)
    monkeypatch.setattr("app.agent.nodes.query_analysis.classify_intent_and_collab", fake_classify)
    # 默认 multi_agent_collab_enabled=False → needs_collab 被 AND 为 False
    out = await query_analysis({"query": "堆积排查"}, {"configurable": {}})
    assert out["intent"] == "bug"
    assert out["needs_collab"] is False


async def test_query_analysis_needs_collab_on_when_enabled(monkeypatch):
    async def fake_rw(q):
        return {"semantic_query": q, "extra_keywords": []}

    async def fake_classify(q, *, pack=None):
        return llm_mod.IntentSchema(intent="bug", needs_collab=True)

    monkeypatch.setattr("app.agent.nodes.query_analysis.rewrite_query", fake_rw)
    monkeypatch.setattr("app.agent.nodes.query_analysis.classify_intent_and_collab", fake_classify)
    monkeypatch.setattr("app.agent.nodes.query_analysis.settings",
                        type("S", (), {"multi_agent_collab_enabled": True})())
    out = await query_analysis({"query": "堆积排查"}, {"configurable": {}})
    assert out["needs_collab"] is True


# ---- router collab 守卫 ----


async def test_route_collab_when_enabled_and_needed(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    monkeypatch.setattr(router_mod, "settings",
                        type("S", (), {"multi_agent_collab_enabled": True})())
    assert router_mod.route({"intent": "bug", "needs_collab": True}) == "collab"


async def test_route_skips_collab_when_disabled(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    # 默认 settings.multi_agent_collab_enabled=False
    assert router_mod.route({"intent": "bug", "needs_collab": True}) == "bug_diagnosis"


async def test_route_skips_collab_when_not_needed(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    monkeypatch.setattr(router_mod, "settings",
                        type("S", (), {"multi_agent_collab_enabled": True})())
    assert router_mod.route({"intent": "code", "needs_collab": False}) == "code_understand"


async def test_route_skips_collab_when_not_configured(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: False)
    monkeypatch.setattr(router_mod, "settings",
                        type("S", (), {"multi_agent_collab_enabled": True})())
    assert router_mod.route({"intent": "bug", "needs_collab": True}) == "retrieve"
