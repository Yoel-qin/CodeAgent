"""AgentRegistry / AgentSpec 单测（M33）—— 纯逻辑，不触 LangGraph/DB。"""
from __future__ import annotations

from app.agent.registry import AgentSpec, AgentRegistry


def _spec(agent_type="X", node_name="x", intent=None, route_guard=None):
    return AgentSpec(
        agent_type=agent_type, node_name=node_name,
        node_fn=lambda **_: None, intent=intent, route_guard=route_guard,
    )


def test_register_and_get():
    r = AgentRegistry()
    s = _spec("CODE_UNDERSTAND", "code_understand", intent="code")
    r.register(s)
    assert r.get("CODE_UNDERSTAND") is s
    assert r.get("MISSING") is None


def test_specs_returns_all():
    r = AgentRegistry()
    r.register(_spec("A", "a"))
    r.register(_spec("B", "b"))
    assert {s.agent_type for s in r.specs()} == {"A", "B"}


def test_route_target_by_agent_type():
    r = AgentRegistry()
    r.register(_spec("CODE_UNDERSTAND", "code_understand", intent="code"))
    assert r.route_target(agent_type="CODE_UNDERSTAND", intent=None) == "code_understand"


def test_route_target_by_intent_when_no_agent_type():
    r = AgentRegistry()
    r.register(_spec("CODE_UNDERSTAND", "code_understand", intent="code"))
    assert r.route_target(agent_type=None, intent="code") == "code_understand"


def test_route_target_agent_type_takes_precedence_and_does_not_consult_intent():
    """复刻旧 router：显式 agent_type 一旦给出（即使是未知值）就不再查 intent。"""
    r = AgentRegistry()
    r.register(_spec("CODE_UNDERSTAND", "code_understand", intent="code"))
    assert r.route_target(agent_type="CODE_UNDERSTAND", intent="doc") == "code_understand"
    assert r.route_target(agent_type="UNKNOWN", intent="code") == "retrieve"


def test_route_target_no_match_returns_retrieve():
    r = AgentRegistry()
    assert r.route_target(agent_type=None, intent="chitchat") == "retrieve"
    assert r.route_target(agent_type=None, intent=None) == "retrieve"


def test_route_target_guard_false_returns_retrieve():
    r = AgentRegistry()
    r.register(_spec("WEB_SEARCH", "web_search", route_guard=lambda: False))
    assert r.route_target(agent_type="WEB_SEARCH", intent=None) == "retrieve"


def test_route_target_guard_true_proceeds():
    r = AgentRegistry()
    r.register(_spec("WEB_SEARCH", "web_search", route_guard=lambda: ["t"]))
    assert r.route_target(agent_type="WEB_SEARCH", intent=None) == "web_search"
