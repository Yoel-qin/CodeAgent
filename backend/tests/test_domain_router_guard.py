"""M37 router 领域守卫：领域 intent 无包/包不含 → retrieve；有包含 → route_target 命中。"""
from __future__ import annotations

from app.agent.nodes import router as router_mod
from app.domain_packs.models import DomainPack, Manifest


def _reg_with(pack: DomainPack | None):
    """构造一个 fake domain registry，get(name) 返 pack。"""
    if pack is None:
        return type("R", (), {"get": lambda self, name: None})()
    return type("R", (), {"get": lambda self, name: pack if name == pack.manifest.name else None})()


def _pack(active_agents: list[str]) -> DomainPack:
    return DomainPack(manifest=Manifest(name="rocketmq", target_repo="apache/rocketmq",
                                        active_agents=active_agents))


def test_domain_intent_no_pack_falls_to_retrieve(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    monkeypatch.setattr("app.agent.nodes.router.settings", type("S", (), {"multi_agent_collab_enabled": False})())
    assert router_mod.route({"intent": "trace"}) == "retrieve"
    assert router_mod.route({"intent": "diagnose"}) == "retrieve"


def test_domain_intent_pack_without_agent_falls_to_retrieve(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    monkeypatch.setattr("app.agent.nodes.router.settings", type("S", (), {"multi_agent_collab_enabled": False})())
    # 包激活但 active_agents 不含 trace
    monkeypatch.setattr(router_mod, "_get_pack_registry",
                        lambda: _reg_with(_pack(active_agents=["diagnose", "tune"])))
    assert router_mod.route({"intent": "trace", "active_pack_name": "rocketmq"}) == "retrieve"


def test_domain_intent_pack_with_agent_hits_node(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    monkeypatch.setattr("app.agent.nodes.router.settings", type("S", (), {"multi_agent_collab_enabled": False})())
    monkeypatch.setattr(router_mod, "_get_pack_registry",
                        lambda: _reg_with(_pack(active_agents=["trace", "diagnose", "tune"])))
    # route_target 经 AgentRegistry 命中 trace_route（Task 4 已登记）
    out = router_mod.route({"intent": "trace", "active_pack_name": "rocketmq"})
    assert out == "trace_route"


def test_non_domain_intent_unaffected(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    monkeypatch.setattr("app.agent.nodes.router.settings", type("S", (), {"multi_agent_collab_enabled": False})())
    # code intent 不受守卫影响（无 active_pack_name 也照常 route_target）
    assert router_mod.route({"intent": "code"}) == "code_understand"
    assert router_mod.route({"intent": "bug"}) == "bug_diagnosis"
