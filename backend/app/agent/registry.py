"""场景 Agent 注册中心（M33）：Agent 元信息的单一真相源。

集中原本散落于各 agent 模块（PROMPT/TOOLS/节点函数）+ ``router`` 两张分发表
（``_AGENT_TYPE_TO_NODE`` / ``_INTENT_TO_AGENT_TYPE``）的元信息。本里程碑只做
「登记 + ``router.route`` 改查表」——**行为与旧硬编码表完全等价**（见
``tests/test_agent_registry.py`` 的 route_target 分支复刻旧 ``or`` 语义）。

不删任何现有 agent 模块（渐进迁移）；``graph.py`` 不改（节点函数不变）。
YAML 声明式定义留下个 plan。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentSpec:
    """单个场景 Agent 的声明式元信息。

    必填：``agent_type`` / ``node_name`` / ``node_fn``（主图节点函数）。
    可选：``intent``（不接 intent 的 Agent 留 None，仅靠显式 agent_type）；
          ``route_guard``（ callable→bool ，返回假值时 ``route_target`` 回落 ``retrieve``，
          用于 WEB_SEARCH「工具未就绪」）；``tools``/``prompt``/``build_agent``/``degrade_label``
          为「元信息集中」预留，本里程碑不强求填全（渐进迁移）。
    """
    agent_type: str
    node_name: str
    node_fn: Callable
    intent: str | None = None
    tools: list = field(default_factory=list)
    prompt: str = ""
    degrade_label: str = ""
    build_agent: Callable[[], object] | None = None
    route_guard: Callable[[], bool] | None = None


class AgentRegistry:
    """Agent 注册表。``register`` 幂等（同 agent_type 覆盖），便于测试与重入。"""

    def __init__(self) -> None:
        self._by_type: dict[str, AgentSpec] = {}

    def register(self, spec: AgentSpec) -> None:
        self._by_type[spec.agent_type] = spec

    def get(self, agent_type: str) -> AgentSpec | None:
        return self._by_type.get(agent_type)

    def specs(self) -> list[AgentSpec]:
        return list(self._by_type.values())

    def route_target(self, *, agent_type: str | None, intent: str | None) -> str:
        """返回目标节点名；无匹配 / guard 假 → ``retrieve``。

        语义复刻旧 ``router.route``：``agent_type`` 一旦给出（真值）就只按它查、不查 intent
        （与旧 ``state.get("agent_type") or _INTENT_TO_AGENT_TYPE.get(intent)`` 的 ``or`` 短路一致）；
        ``agent_type`` 缺省时才按 intent 查。
        """
        spec: AgentSpec | None = None
        if agent_type:
            spec = self._by_type.get(agent_type)
        elif intent:
            spec = next((s for s in self._by_type.values() if s.intent == intent), None)
        if spec is None:
            return "retrieve"
        if spec.route_guard is not None and not spec.route_guard():
            return "retrieve"
        return spec.node_name


_registry = AgentRegistry()
_registered = False


def register(spec: AgentSpec) -> None:
    """往模块单例登记一个 AgentSpec。"""
    _registry.register(spec)


def get_registry() -> AgentRegistry:
    """全局单例；首次访问触发 ``registry_data`` 登记现有 8 个 Agent（import 副作用，仅一次）。"""
    global _registered
    if not _registered:
        from app.agent import registry_data  # noqa: F401 —— 触发登记
        _registered = True
    return _registry
