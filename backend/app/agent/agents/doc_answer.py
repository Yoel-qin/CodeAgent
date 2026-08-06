"""文档问答 Agent（Phase 7 Milestone 3；M4 改用 ``_base.run_scenario_agent`` 共享骨架）。

用 LangGraph 预置的 ``create_react_agent`` 绑定 3 个文档工具。主图里的 ``doc_answer`` 节点是
薄包装：转调 ``_base.run_scenario_agent``，节点逻辑与 ``code_understand``/``change_impact`` 同构
（``_degrade``/wrapper 文档无关，已收敛到 ``_base``）。

工具侧（见 ``tools/doc_tools.py``）经 ``get_stream_writer`` 推 ``agent_step`` + 逐条 ``citation``，
故引用由适配器从事件累积，无需 Command / 图 state 写入。
"""
from __future__ import annotations

import warnings

from langchain_core.runnables import RunnableConfig

from app.agent.agents._base import run_scenario_agent
from app.agent.llm import get_chat_model
from app.agent.state import AgentState
from app.agent.tools.doc_tools import DOC_TOOLS

DOC_ANSWER_PROMPT = (
    "你是 CodeRAG 的【文档问答 Agent】，擅长回答关于设计文档、配置、概念、用法的问题。\n"
    "工作方式（ReAct）：先用工具检索文档段落，观察结果，按需拉取关联代码作佐证，再作答。\n"
    "可用工具：search_docs（语义检索文档段落：设计文档/配置/说明）、read_doc（精读某文档段落："
    "全文+章节+表格/图片+出处）、get_related_code（查找文档段落关联的实现代码，作佐证）。\n"
    "规则：① 回答必须基于工具检索到的文档/代码，不要臆造；② 引用文档须标注出处章节（heading）"
    "与 chunk_id；③ 配置/概念类问题优先用文档，需要代码佐证时再用 get_related_code；"
    "④ 一般 2-4 步工具调用即可作答，避免冗余，**不要重复读取同一个 chunk**；⑤ 用中文、简洁，代码用代码块。"
)

_agent = None


def get_doc_agent():
    """惰性单例：create_react_agent（默认 state_schema，绑定文档工具）。"""
    global _agent
    if _agent is None:
        with warnings.catch_warnings():
            # langgraph-prebuilt 的 create_react_agent 在 v1 标记弃用（迁往 langchain.agents），
            # 但 langchain 包未安装，功能在 langgraph 内仍完整。抑制该告警保持日志干净。
            warnings.simplefilter("ignore")
            from langgraph.prebuilt import create_react_agent
            _agent = create_react_agent(get_chat_model(), DOC_TOOLS, prompt=DOC_ANSWER_PROMPT)
    return _agent


async def doc_answer(state: AgentState, config: RunnableConfig) -> dict:
    """主图节点：跑文档问答自动 Agent（前置 retrieval meta、token 回调、异常兜底）。"""
    return await run_scenario_agent(
        state, config,
        agent_name="DOC_ANSWER", tools=DOC_TOOLS,
        build_agent=get_doc_agent, degrade_label="文档问答",
    )
