"""联网检索 Agent（WEB_SEARCH）—— 调用远程/在线 MCP server 回答知识库之外的问题。

工具集来自 MCP server，lifespan 启动期经 ``tools/web_tools.py`` load+wrap 缓存。MCP 未启用/不可达
→ ``get_web_tools()`` 返空 → ``router.route`` 把 web 意图回落 ``retrieve``（不进本节点）；即便经显式
``agent_type=WEB_SEARCH`` 进了本节点且工具为空，``run_scenario_agent`` 的异常兜底会走 ``_degrade``
（KB 检索），请求不崩。

节点结构与 ``doc_answer``/``code_review`` 同构：薄包装转调 ``_base.run_scenario_agent``。
联网结果按既定决策**只发 ``agent_step`` 轨迹、不发 citation**（前端零改动，右侧轨迹可追溯）。
"""
from __future__ import annotations

import warnings

from langchain_core.runnables import RunnableConfig

from app.agent.agents._base import run_scenario_agent
from app.agent.llm import get_chat_model
from app.agent.state import AgentState
from app.agent.tools.web_tools import get_web_tools

WEB_PROMPT = (
    "你是 CodeRAG 的【联网检索 Agent】，负责回答**知识库之外**的问题：最新资讯、官方文档、"
    "第三方库用法、外部概念等。\n"
    "工作方式（ReAct）：先用联网工具检索/抓取，观察结果，再作答。\n"
    "规则：① 回答须基于工具检索到的联网内容，不要臆造；② 在文本里以 URL 注明来源；"
    "③ 若工具返回失败或无可用工具，如实说明「联网检索未启用或不可达」，不要编造；"
    "④ 用中文、简洁，代码用代码块。"
)

_agent = None


def get_web_agent():
    """惰性单例：create_react_agent（绑定 lifespan 缓存的联网工具集）。

    与其他场景 Agent 不同：工具为空时返回 None（不建空 Agent）；该情形下 ``run_scenario_agent``
    会因 ``None.astream`` 抛错而走 ``_degrade`` 兜底，请求不崩。
    """
    global _agent
    if _agent is None:
        tools = get_web_tools()
        if not tools:
            return None
        with warnings.catch_warnings():
            # langgraph-prebuilt 的 create_react_agent 在 v1 标记弃用（迁往 langchain.agents），
            # 但 langchain 包未安装，功能在 langgraph 内仍完整。抑制该告警保持日志干净（与其他 Agent 一致）。
            warnings.simplefilter("ignore")
            from langgraph.prebuilt import create_react_agent
            _agent = create_react_agent(get_chat_model(), tools, prompt=WEB_PROMPT)
    return _agent


async def web_search(state: AgentState, config: RunnableConfig) -> dict:
    """主图节点：跑联网检索自动 Agent（前置 retrieval meta、token 回调、异常兜底）。"""
    return await run_scenario_agent(
        state, config,
        agent_name="WEB_SEARCH", tools=get_web_tools(),
        build_agent=get_web_agent, degrade_label="联网检索",
    )
