"""M37 故障诊断 Agent 节点（领域 Agent，包驱动 prompt：base + diagnosis_trees 注入）。"""
from __future__ import annotations

import warnings

from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent

from app.agent.agents._base import run_scenario_agent
from app.agent.agents._domain_prompt import _pack_from_state, build_domain_prompt
from app.agent.llm import get_chat_model
from app.agent.state import AgentState
from app.agent.tools.code_tools import (
    get_callers,
    get_recent_changes,
    get_related_docs,
    read_code,
    search_code,
    search_symbol,
)

#: 故障诊断工具集（定位 + 上游影响 + 回归排查 + 关联文档 + 精读）
DIAGNOSE_TOOLS = [search_code, search_symbol, get_callers, get_recent_changes, read_code, get_related_docs]


async def diagnose(state: AgentState, config: RunnableConfig) -> dict:
    """主图节点：组 diagnose prompt → 闭包 build_agent → 跑场景 Agent 骨架。"""
    pack = _pack_from_state(state)
    prompt = build_domain_prompt("diagnose", pack)

    def build_agent():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return create_react_agent(get_chat_model(), DIAGNOSE_TOOLS, prompt=prompt)

    return await run_scenario_agent(
        state, config,
        agent_name="DIAGNOSE", tools=DIAGNOSE_TOOLS, build_agent=build_agent, degrade_label="故障诊断",
    )
