"""M37 领域 Agent 节点：组 prompt（含 pack 知识）+ 转调 run_scenario_agent。"""
from __future__ import annotations

import warnings

import pytest

from app.domain_packs.models import DiagnosisTree, DomainPack, Manifest, TraceTemplate, TuningRule


def _pack() -> DomainPack:
    return DomainPack(
        manifest=Manifest(name="rocketmq", target_repo="apache/rocketmq"),
        trace_templates=[TraceTemplate(name="normal_send", method_sequence=["DefaultMQProducer.send"])],
        diagnosis_trees=[DiagnosisTree(name="msg_accumulation", symptoms=["消息堆积"])],
        tuning_rules=[TuningRule(scenario="high_throughput", parameter="maxReconsumeTimes", suggestion="增加最大重试次数")],
    )


@pytest.mark.asyncio
async def test_trace_route_node(monkeypatch):
    from app.agent.agents import trace_route as mod
    monkeypatch.setattr(mod, "_pack_from_state", lambda state: _pack())
    monkeypatch.setattr(mod, "get_chat_model", lambda: "FAKE_MODEL")
    prompt_box = {}

    # Mock agent with astream method
    class MockAgent:
        def __init__(self, prompt):
            self.prompt = prompt
            prompt_box["v"] = prompt

        async def astream(self, *args, **kwargs):
            return
            yield  # Make it an async generator

    monkeypatch.setattr(mod, "create_react_agent",
                        lambda model, tools, *, prompt: MockAgent(prompt))
    captured = {}

    async def fake_run(state, config, *, agent_name, tools, build_agent, degrade_label):
        captured.update(agent_name=agent_name, tools=tools, degrade_label=degrade_label)
        build_agent()                       # 触发 create_react_agent，捕获 prompt
        return {}

    monkeypatch.setattr(mod, "run_scenario_agent", fake_run)
    with warnings.catch_warnings():
        await mod.trace_route({"query": "q"}, {})
    assert captured["agent_name"] == "TRACE_ROUTE"
    assert captured["degrade_label"] == "链路追踪"
    assert captured["tools"] == mod.TRACE_TOOLS
    assert "normal_send" in prompt_box["v"]            # pack 知识注入
    assert "链路追踪" in prompt_box["v"]                # base 角色


@pytest.mark.asyncio
async def test_diagnose_node(monkeypatch):
    from app.agent.agents import diagnose as mod
    monkeypatch.setattr(mod, "_pack_from_state", lambda state: _pack())
    monkeypatch.setattr(mod, "get_chat_model", lambda: "FAKE_MODEL")

    # Mock agent with astream method
    class MockAgent:
        async def astream(self, *args, **kwargs):
            return
            yield

    monkeypatch.setattr(mod, "create_react_agent", lambda model, tools, *, prompt: MockAgent())
    captured = {}

    async def fake_run(state, config, *, agent_name, tools, build_agent, degrade_label):
        captured.update(agent_name=agent_name, tools=tools, degrade_label=degrade_label)
        return {}

    monkeypatch.setattr(mod, "run_scenario_agent", fake_run)
    await mod.diagnose({"query": "q"}, {})
    assert captured["agent_name"] == "DIAGNOSE"
    assert captured["degrade_label"] == "故障诊断"
    assert captured["tools"] == mod.DIAGNOSE_TOOLS


@pytest.mark.asyncio
async def test_tune_node(monkeypatch):
    from app.agent.agents import tune as mod
    monkeypatch.setattr(mod, "_pack_from_state", lambda state: _pack())
    monkeypatch.setattr(mod, "get_chat_model", lambda: "FAKE_MODEL")

    # Mock agent with astream method
    class MockAgent:
        async def astream(self, *args, **kwargs):
            return
            yield

    monkeypatch.setattr(mod, "create_react_agent", lambda model, tools, *, prompt: MockAgent())
    captured = {}

    async def fake_run(state, config, *, agent_name, tools, build_agent, degrade_label):
        captured.update(agent_name=agent_name, tools=tools, degrade_label=degrade_label)
        return {}

    monkeypatch.setattr(mod, "run_scenario_agent", fake_run)
    await mod.tune({"query": "q"}, {})
    assert captured["agent_name"] == "TUNE"
    assert captured["degrade_label"] == "性能调优"
    assert captured["tools"] == mod.TUNE_TOOLS
