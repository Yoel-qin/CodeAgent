"""变更影响 Agent（Phase 7 Milestone 4）。

回答『修改某段代码会波及哪些地方』。用 LangGraph 预置的 ``create_react_agent`` 绑定 5 个工具
（定位 + 确认 + 调用图展开），节点是薄包装转调 ``_base.run_scenario_agent``
（与 ``code_understand``/``doc_answer`` 同构）。工具侧（``tools/code_tools.py``）经
``get_stream_writer`` 推 ``agent_step`` + 逐条 ``citation``，引用由适配器从事件累积。

意图路由：``graph`` 意图（依赖/结构/影响范围）→ ``change_impact`` 节点。
"""
from __future__ import annotations

import warnings

from langchain_core.runnables import RunnableConfig

from app.agent.agents._base import run_scenario_agent
from app.agent.llm import get_chat_model
from app.agent.state import AgentState
from app.agent.tools.code_tools import (
    get_call_chain,
    get_callers,
    read_code,
    search_code,
    search_symbol,
)

CHANGE_IMPACT_PROMPT = (
    "你是 CodeRAG 的【变更影响 Agent】，擅长评估『修改某段代码会波及哪些地方』。\n"
    "工作方式（ReAct）：先定位目标（search_symbol 按名 / search_code 按描述）→ read_code 确认其实现 → "
    "用 get_callers（上游影响面：谁调用了它）或 get_call_chain(direction=CALLERS/CALLEES/BOTH) "
    "展开调用链 → 归纳影响范围。\n"
    "可用工具：search_symbol、search_code、read_code、get_call_chain、get_callers。\n"
    "规则：① 影响面结论必须基于调用图检索结果，不要臆造；② 先给『直接受影响的调用方』再给"
    "『间接/跨层』，按层归纳、标注 chunk_id；③ 目标不明确时先 search_symbol **一次**解析出 center id，"
    "拿到 center 后**立即**用 get_callers（或 get_call_chain direction=CALLERS）展开**一次**影响面，"
    "不要反复搜索/读取同一目标；④ 广泛被调方法用 get_callers（上限更高），小范围用 get_call_chain；"
    "⑤ 用中文、简洁，控制在 4 步内，**不要重复读取同一个 chunk**，代码用代码块。"
)

#: 变更影响 Agent 绑定的工具集（定位 + 确认 + 调用图展开；复用代码工具 + get_callers 放上限）
IMPACT_TOOLS = [search_symbol, search_code, read_code, get_call_chain, get_callers]

_agent = None


def get_change_impact_agent():
    """惰性单例：create_react_agent（默认 state_schema，绑定变更影响工具集）。"""
    global _agent
    if _agent is None:
        with warnings.catch_warnings():
            # langgraph-prebuilt 的 create_react_agent 在 v1 标记弃用（迁往 langchain.agents），
            # 但 langchain 包未安装，功能在 langgraph 内仍完整。抑制该告警保持日志干净。
            warnings.simplefilter("ignore")
            from langgraph.prebuilt import create_react_agent
            _agent = create_react_agent(get_chat_model(), IMPACT_TOOLS, prompt=CHANGE_IMPACT_PROMPT)
    return _agent


async def change_impact(state: AgentState, config: RunnableConfig) -> dict:
    """主图节点：跑变更影响自动 Agent（前置 retrieval meta、token 回调、异常兜底）。"""
    return await run_scenario_agent(
        state, config,
        agent_name="CHANGE_IMPACT", tools=IMPACT_TOOLS,
        build_agent=get_change_impact_agent, degrade_label="变更影响",
    )
