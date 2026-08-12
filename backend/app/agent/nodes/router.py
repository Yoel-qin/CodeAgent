"""Orchestrator 条件路由：意图/agent_type → Agent 节点（M33 起改查 AgentRegistry）。

``route`` 从 :func:`app.agent.registry.get_registry` 查目标节点（``agent_type`` 优先于
``intent``；WEB_SEARCH 工具未就绪 / 无匹配 → ``retrieve``）。语义与旧两张硬编码分发表
（``_AGENT_TYPE_TO_NODE`` / ``_INTENT_TO_AGENT_TYPE``）完全等价——route_target 在 Registry 内
复刻了旧 ``or`` 短路与 WEB_SEARCH 特判（现以 ``AgentSpec.route_guard`` 表达）。未配置 LLM
时不进任何 Agent（走 retrieve→generate，其自身降级）。

加一个场景 Agent = 在 ``registry_data.py`` 登记一行 + ``graph.py`` 加节点/边（本文件不再需要改）。
"""
from __future__ import annotations

from app.agent.llm import configured
from app.agent.registry import get_registry
from app.agent.state import AgentState


def route(state: AgentState) -> str:
    """返回下一节点名：某场景 Agent 节点 | retrieve（兜底）。

    优先级：显式 agent_type > 意图；未配置 LLM / 无匹配 / guard 假 → retrieve。
    """
    if not configured():
        return "retrieve"
    return get_registry().route_target(
        agent_type=state.get("agent_type"),
        intent=state.get("intent"),
    )
