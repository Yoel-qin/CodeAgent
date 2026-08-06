"""代码理解 Agent（Phase 7 Milestone 2；M4 改用 ``_base.run_scenario_agent`` 共享骨架）。

用 LangGraph 预置的 ``create_react_agent``（自动 Agent：LLM 自主选工具、循环到能作答）绑定 5 个
代码检索/图遍历工具。主图里的 ``code_understand`` 节点是薄包装：转调 ``_base.run_scenario_agent``
（前置 retrieval meta、token 回调、异常兜底），节点逻辑与 ``doc_answer``/``change_impact`` 同构。

工具侧（见 ``tools/code_tools.py``）经 ``get_stream_writer`` 推 ``agent_step`` + 逐条 ``citation``，
故引用由适配器从事件累积，无需 Command / 图 state 写入。
"""
from __future__ import annotations

import warnings

from langchain_core.runnables import RunnableConfig

from app.agent.agents._base import run_scenario_agent
from app.agent.llm import get_chat_model
from app.agent.state import AgentState
from app.agent.tools.code_tools import TOOLS

CODE_UNDERSTAND_PROMPT = (
    "你是 CodeRAG 的【代码理解 Agent】，擅长解释 Java 代码：方法的职责、调用关系、设计意图。\n"
    "工作方式（ReAct）：先用工具检索/定位，观察结果，再决定下一步，最后作答。\n"
    "可用工具：search_code（语义检索代码）、search_symbol（按名解析类/方法 id）、"
    "get_call_chain（展开调用链：谁调用它/它调用谁）、get_related_docs（关联设计文档）、"
    "read_code（精读某片段全文+签名+Javadoc）。\n"
    "规则：① 回答必须基于工具检索到的代码/文档，不要臆造；② 引用时用 chunk_id 或 类名.方法名；"
    "③ 一般 2-4 步工具调用即可作答，避免冗余；④ 用中文、简洁，代码用代码块。"
)

_agent = None


def get_code_agent():
    """惰性单例：create_react_agent（默认 state_schema，绑定代码工具）。"""
    global _agent
    if _agent is None:
        with warnings.catch_warnings():
            # langgraph-prebuilt 的 create_react_agent 在 v1 标记弃用（迁往 langchain.agents），
            # 但 langchain 包未安装，功能在 langgraph 内仍完整。抑制该告警保持日志干净。
            warnings.simplefilter("ignore")
            from langgraph.prebuilt import create_react_agent
            _agent = create_react_agent(get_chat_model(), TOOLS, prompt=CODE_UNDERSTAND_PROMPT)
    return _agent


async def code_understand(state: AgentState, config: RunnableConfig) -> dict:
    """主图节点：跑代码理解自动 Agent（前置 retrieval meta、token 回调、异常兜底）。"""
    return await run_scenario_agent(
        state, config,
        agent_name="CODE_UNDERSTAND", tools=TOOLS,
        build_agent=get_code_agent, degrade_label="代码理解",
    )
