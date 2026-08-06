"""缺陷诊断 Agent（Phase 7 Milestone 6）。

回答『某段代码为什么出错 / 在什么情况下会失败 / 报错的根因』。用 LangGraph 预置的
``create_react_agent`` 绑定 6 个工具（定位 + 精读 + 调用图展开 + 回归排查），节点是薄包装
转调 ``_base.run_scenario_agent``（与 ``code_understand``/``doc_answer``/``change_impact`` 同构）。
工具侧（``tools/code_tools.py``）经 ``get_stream_writer`` 推 ``agent_step`` + 逐条 ``citation``，
引用由适配器从事件累积。

意图路由：``bug`` 意图（报错/异常/崩溃/为何失败）→ ``bug_diagnosis`` 节点。
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
    get_recent_changes,
    read_code,
    search_code,
    search_symbol,
)

BUG_DIAGNOSIS_PROMPT = (
    "你是 CodeRAG 的【缺陷诊断 Agent】，擅长根据症状/报错定位代码根因。\n"
    "工作方式（ReAct）：先定位疑似代码（search_symbol 按名 / search_code 按症状描述）→ "
    "read_code 精读其实现 → 用 get_call_chain / get_callers 追踪调用上下文（谁触发它、它依赖谁）→ "
    "get_recent_changes 查该代码近期是否被改动（回归排查）→ 给出最可能的根因与修复建议。\n"
    "可用工具：search_symbol、search_code、read_code、get_call_chain、get_callers、get_recent_changes。\n"
    "规则：① 根因结论必须基于检索/读取到的真实代码，不要臆造；② 先列出 1-3 个候选根因并标注证据"
    "（chunk_id / 调用关系 / 近期变更），再给修复建议；③ 目标不明确时先 search_symbol **一次**解析"
    "出 center id，拿到后**立即** read_code 确认，不要反复搜索/读取同一目标；④ 若 get_recent_changes"
    "返回『无变更历史』，说明无回归线索，转而从代码逻辑本身排查，不要纠结；⑤ 用中文、简洁，"
    "控制在 6 步内，**不要重复读取同一个 chunk**，代码用代码块。"
)

#: 缺陷诊断 Agent 绑定的工具集（定位 + 精读 + 调用图展开 + 回归排查；复用代码工具 + get_recent_changes）
BUG_TOOLS = [search_symbol, search_code, read_code, get_call_chain, get_callers, get_recent_changes]

_agent = None


def get_bug_diagnosis_agent():
    """惰性单例：create_react_agent（默认 state_schema，绑定缺陷诊断工具集）。"""
    global _agent
    if _agent is None:
        with warnings.catch_warnings():
            # langgraph-prebuilt 的 create_react_agent 在 v1 标记弃用（迁往 langchain.agents），
            # 但 langchain 包未安装，功能在 langgraph 内仍完整。抑制该告警保持日志干净。
            warnings.simplefilter("ignore")
            from langgraph.prebuilt import create_react_agent
            _agent = create_react_agent(get_chat_model(), BUG_TOOLS, prompt=BUG_DIAGNOSIS_PROMPT)
    return _agent


async def bug_diagnosis(state: AgentState, config: RunnableConfig) -> dict:
    """主图节点：跑缺陷诊断自动 Agent（前置 retrieval meta、token 回调、异常兜底）。"""
    return await run_scenario_agent(
        state, config,
        agent_name="BUG_DIAGNOSIS", tools=BUG_TOOLS,
        build_agent=get_bug_diagnosis_agent, degrade_label="缺陷诊断",
    )
