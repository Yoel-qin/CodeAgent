"""M37 链路追踪 Agent 节点（领域 Agent，包驱动 prompt）。

与通用 scenario agent 区别：prompt 请求期从激活包组装（base 角色 + trace_templates 注入），
故不用模块单例——每请求建 create_react_agent（构造便宜）。节点逻辑仍转调 _base.run_scenario_agent
（前置 retrieval meta、token 回调、异常兜底），run_scenario_agent 签名零改。
"""
from __future__ import annotations

import warnings

from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent

from app.agent.agents._base import run_scenario_agent
from app.agent.agents._domain_prompt import _pack_from_state, build_domain_prompt
from app.agent.llm import get_chat_model
from app.agent.state import AgentState
from app.agent.tools.code_tools import (
    get_call_chain,
    get_downstream_callers,
    read_code,
    search_code,
    search_symbol,
)

#: 链路追踪工具集（复用 code_tools 现有 @tool：双向调用链 + 定位 + 精读）
TRACE_TOOLS = [search_code, search_symbol, get_call_chain, get_downstream_callers, read_code]


async def trace_route(state: AgentState, config: RunnableConfig) -> dict:
    """主图节点：组 trace prompt（base + pack 注入）→ 闭包 build_agent → 跑场景 Agent 骨架。"""
    pack = _pack_from_state(state)
    prompt = build_domain_prompt("trace", pack)

    def build_agent():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")   # 抑制 create_react_agent v1 弃用告警
            return create_react_agent(get_chat_model(), TRACE_TOOLS, prompt=prompt)

    return await run_scenario_agent(
        state, config,
        agent_name="TRACE_ROUTE", tools=TRACE_TOOLS, build_agent=build_agent, degrade_label="链路追踪",
    )
