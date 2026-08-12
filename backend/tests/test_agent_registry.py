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


def test_registry_data_registers_all_agents():
    """get_registry() 触发 registry_data 登记：7 场景 + DOC_MAINTAIN，intent/node 映射正确。"""
    from app.agent.registry import get_registry

    r = get_registry()
    assert r.get("CODE_UNDERSTAND").intent == "code"
    assert r.get("CODE_UNDERSTAND").node_name == "code_understand"
    assert r.get("DOC_ANSWER").intent == "doc"
    assert r.get("CHANGE_IMPACT").intent == "graph"
    assert r.get("BUG_DIAGNOSIS").intent == "bug"
    assert r.get("CODE_REVIEW").intent == "review"
    assert r.get("TEST_GENERATION").intent == "test"
    assert r.get("WEB_SEARCH").intent == "web"
    assert r.get("WEB_SEARCH").route_guard is not None  # 工具空→retrieve
    assert r.get("DOC_MAINTAIN").node_name == "propose"  # HITL 链入口
    assert r.get("DOC_MAINTAIN").intent is None           # 不接 intent


def test_route_target_web_without_tools_falls_back():
    """WEB_SEARCH route_guard 在无工具时回落 retrieve（等价旧 router 特判）。"""
    from app.agent.registry import get_registry
    import app.agent.tools.web_tools as wt

    wt._web_tools = []  # 模拟 MCP 未启用
    assert get_registry().route_target(agent_type="WEB_SEARCH", intent=None) == "retrieve"
    wt._web_tools = [object()]  # 模拟有工具
    assert get_registry().route_target(agent_type="WEB_SEARCH", intent=None) == "web_search"
