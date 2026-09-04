"""WebSearch 场景节点（M9）——``route == "web_search"`` 的 ReAct 主体。

与 :mod:`app.agent.codenav` 同构（薄节点转调 ``run_react_agent``）；工具为 lifespan
加载的远程 MCP 工具（未配置/不可达 → ``get_web_tools()`` 空 → react_base 空工具分支
自动降级 retrieve——降级链零新增代码）。web 工具不发 citation（外网内容非 KB chunk，
``_extract_citations`` 未知工具恒空——v1 同契约，前端零改）。
"""
# 注意：本模块**不**加 ``from __future__ import annotations``（config 注入白名单，同 codenav）。
from langchain_core.runnables import RunnableConfig

from app.agent.prompts import WEB_SEARCH_SYSTEM
from app.agent.react_base import run_react_agent
from app.agent.state import AgentState
from app.agent.tools_loader import get_web_tools
from app.core.config import settings

__all__ = ["web_search_node"]


async def web_search_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """联网检索节点：远程 MCP 工具 ReAct；空工具（未配置/不可达）自动降级 retrieve。"""
    cfg = (config or {}).get("configurable") or {}
    return await run_react_agent(
        state, config,
        agent_name="web_search",
        tools=get_web_tools(),
        system_prompt=WEB_SEARCH_SYSTEM,
        max_rounds=cfg.get("rounds_web") or settings.agent_rounds_web,
        degrade_label="WebSearch",
    )
