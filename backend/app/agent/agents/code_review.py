"""代码审查 Agent（Phase 7 Milestone 11）。

主动评估某段代码的质量、发现潜在问题、给可执行的改进建议（区别于 code=解释逻辑 /
bug=诊断已报告的失败 / graph=影响范围）。用 LangGraph 预置的 ``create_react_agent`` 绑定 6 个工具
（定位 + 精读 + 量化度量 + 调用上下文 + 回归排查），节点是薄包装转调 ``_base.run_scenario_agent``
（与 ``code_understand``/``bug_diagnosis`` 等同构）。工具侧（``tools/code_tools.py``）经
``get_stream_writer`` 推 ``agent_step`` + 逐条 ``citation``，引用由适配器从事件累积。

意图路由：``review`` 意图（代码审查/质量评估/改进建议/重构）→ ``code_review`` 节点。
"""
from __future__ import annotations

import warnings

from langchain_core.runnables import RunnableConfig

from app.agent.agents._base import run_scenario_agent
from app.agent.llm import get_chat_model
from app.agent.state import AgentState
from app.agent.tools.code_tools import (
    get_call_chain,
    get_code_metrics,
    get_recent_changes,
    read_code,
    search_code,
    search_symbol,
)

CODE_REVIEW_PROMPT = (
    "你是 CodeRAG 的【代码审查 Agent】，擅长评估代码质量、发现潜在问题并给可执行的改进建议。\n"
    "工作方式（ReAct）：先定位目标（search_symbol 按名 / search_code 按描述）→ read_code 精读其完整源码 → "
    "get_code_metrics 取量化度量（LOC / token / fan-in / fan-out）佐证复杂度与影响面 → "
    "get_call_chain 看调用上下文（它依赖谁、谁依赖它）→ get_recent_changes 看近期改动（高频改动重点查回归）→ "
    "综合给出审查结论。\n"
    "可用工具：search_symbol、search_code、read_code、get_code_metrics、get_call_chain、get_recent_changes。\n"
    "规则：① 结论必须基于检索/读取到的真实代码，不要臆造；② 按**严重度**分级列问题"
    "（正确性 / 错误处理 / 复杂度 / 命名 / 安全），每条配证据（chunk_id / 度量数字 / 调用关系）+ 可执行建议；"
    "③ 用 get_code_metrics 引用客观数字佐证（如『方法 180 行偏长』『fan-in=23 改动需谨慎』）；"
    "④ 目标不明确时先 search_symbol **一次**解析出 center id，拿到后**立即** read_code 确认，"
    "不要反复搜索/读取同一目标；⑤ get_recent_changes 若返回『无变更历史』说明无回归线索，转而从代码本身评估；"
    "⑥ 用中文、简洁，控制在 6 步内，**不要重复读取同一个 chunk**，代码用代码块。"
)

#: 代码审查 Agent 绑定的工具集（定位 + 精读 + 量化度量 + 调用上下文 + 回归排查）
REVIEW_TOOLS = [search_symbol, search_code, read_code, get_code_metrics, get_call_chain, get_recent_changes]

_agent = None


def get_code_review_agent():
    """惰性单例：create_react_agent（默认 state_schema，绑定代码审查工具集）。"""
    global _agent
    if _agent is None:
        with warnings.catch_warnings():
            # langgraph-prebuilt 的 create_react_agent 在 v1 标记弃用（迁往 langchain.agents），
            # 但 langchain 包未安装，功能在 langgraph 内仍完整。抑制该告警保持日志干净。
            warnings.simplefilter("ignore")
            from langgraph.prebuilt import create_react_agent
            _agent = create_react_agent(get_chat_model(), REVIEW_TOOLS, prompt=CODE_REVIEW_PROMPT)
    return _agent


async def code_review(state: AgentState, config: RunnableConfig) -> dict:
    """主图节点：跑代码审查自动 Agent（前置 retrieval meta、token 回调、异常兜底）。"""
    return await run_scenario_agent(
        state, config,
        agent_name="CODE_REVIEW", tools=REVIEW_TOOLS,
        build_agent=get_code_review_agent, degrade_label="代码审查",
    )
