"""CodeNav 场景节点（Plan 3 Task 8）——``route == "code"`` 的 ReAct 主体。

薄节点：转调 :func:`app.agent.react_base.run_react_agent`（wrap 计步/循环/citation、
预算、检索降级链全在骨架），本模块只提供 工具集（code-mcp 5 + graph-mcp 4）、
系统提示词、轮数上限与降级文案。测试 monkeypatch 面 = 本模块的 ``get_code_tools``。
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.agent.prompts import CODENAV_SYSTEM
from app.agent.react_base import run_react_agent
from app.agent.state import AgentState
from app.agent.tools_loader import get_code_tools
from app.core.config import settings

__all__ = ["codenav_node"]


async def codenav_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """代码导航节点：4 步分层导航 ReAct（定位→结构→细节→验证），降级链见 react_base。"""
    return await run_react_agent(
        state, config,
        agent_name="codenav",
        tools=get_code_tools(),
        system_prompt=CODENAV_SYSTEM,
        max_rounds=settings.agent_rounds_code,
        degrade_label="CodeNav",
    )
