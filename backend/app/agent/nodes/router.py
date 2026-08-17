"""Orchestrator 条件路由：意图/agent_type → Agent 节点（M33 起改查 AgentRegistry）。

``route`` 从 :func:`app.agent.registry.get_registry` 查目标节点（``agent_type`` 优先于
``intent``；WEB_SEARCH 工具未就绪 / 无匹配 → ``retrieve``）。

M35：multi_agent_collab_enabled 且 needs_collab → collab 子图。
M37：领域 intent（trace/diagnose/tune）须激活包且 ``manifest.active_agents`` 含该 intent，
否则 → ``retrieve``（决策 ②已保证无包不产领域 intent，此处为兜底 + 有包但 active_agents 不含时回落）。
领域激活是请求级状态（state.active_pack_name），不走 M33 的 route_guard（零参、读进程级状态）。
M41：config 带 trace 时记 route span（attrs.target）。
langgraph 条件边对双参 path 函数不传 config 的版本 → config=None 兜底跳过。

加一个场景 Agent = 在 ``registry_data.py`` 登记一行 + ``graph.py`` 加节点/边（本文件不再需要改）。
"""
from __future__ import annotations

from app.agent.llm import configured
from app.agent.registry import get_registry
from app.agent.state import AgentState
from app.core.config import settings
from app.domain_packs.registry import get_registry as _get_pack_registry

_DOMAIN_INTENTS = ("trace", "diagnose", "tune")


def _pack_has_agent(state: AgentState, intent: str) -> bool:
    """激活包是否存在且 manifest.active_agents 含该领域 intent。"""
    name = state.get("active_pack_name")
    if not name:
        return False
    pack = _get_pack_registry().get(name)
    return pack is not None and intent in pack.manifest.active_agents


def _route(state: AgentState) -> str:
    """路由核心逻辑（纯函数，不含 trace）。"""
    if not configured():
        return "retrieve"
    if settings.multi_agent_collab_enabled and state.get("needs_collab"):
        return "collab"
    intent = state.get("intent")
    if intent in _DOMAIN_INTENTS and not _pack_has_agent(state, intent):
        return "retrieve"
    return get_registry().route_target(
        agent_type=state.get("agent_type"),
        intent=intent,
    )


def route(state: AgentState, config=None) -> str:
    """返回下一节点名：collab | 场景 Agent | retrieve。

    M41：config 带 trace 时记 route span（attrs.target）。
    langgraph 条件边对双参 path 函数不传 config 的版本 → config=None 兜底跳过。
    """
    target = _route(state)
    collector = (config or {}).get("configurable", {}).get("trace") if config else None
    if collector is not None:
        collector.record("route", target, 0.0,
                         parent_id=collector.stack_top,
                         attrs={"intent": state.get("intent"),
                                "agent_type": state.get("agent_type")})
    return target
