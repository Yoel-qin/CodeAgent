"""M37 性能调优 Agent 节点（领域 Agent，包驱动 prompt：base + tuning_rules 注入）。"""
from __future__ import annotations

import warnings

from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent

from app.agent.agents._base import run_scenario_agent
from app.agent.agents._domain_prompt import _pack_from_state, build_domain_prompt
from app.agent.llm import get_chat_model
from app.agent.state import AgentState
from app.agent.tools.code_tools import (
    get_code_metrics,
    get_recent_changes,
    read_code,
    search_code,
    search_symbol,
)

#: 性能调优工具集（度量 + 回归排查 + 定位 + 精读）
TUNE_TOOLS = [search_code, search_symbol, get_code_metrics, get_recent_changes, read_code]


async def tune(state: AgentState, config: RunnableConfig) -> dict:
    """主图节点：组 tune prompt → 闭包 build_agent → 跑场景 Agent 骨架。"""
    pack = _pack_from_state(state)
    prompt = build_domain_prompt("tune", pack)

    def build_agent():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return create_react_agent(get_chat_model(), TUNE_TOOLS, prompt=prompt)

    return await run_scenario_agent(
        state, config,
        agent_name="TUNE", tools=TUNE_TOOLS, build_agent=build_agent, degrade_label="性能调优",
    )
