"""DocQA 场景节点（Plan 3 Task 8）——``route == "doc"`` 的 ReAct 主体 + 无引用拒答 v1。

与 :mod:`app.agent.codenav` 同构（薄节点转调 ``run_react_agent``），多一段**收尾检查**：
节点自建 :class:`ToolCallTracker` 传入骨架（设计决策——tracker 由节点层持有，收尾可读），
ReAct 完整跑完（``tracker.reacted``，降级路径不置位）且 ``tracker.citations`` 为空 →
追加一条 token 提示「未找到可引用的文档依据」。这是「无引用拒答」的 v1 落地：
LLM 空手作答（模型无视了检索要求 / 文档库确实没料）时，给用户一个明确的信号，
而 prompt 侧的「无依据明确拒答」（``DOCQA_SYSTEM``）是同一约束的模型侧防线。
降级路径（工具挂/无 key/异常转 retrieve）不追加——retrieve 自产自己的 citation。
"""
# 注意：本模块**不**加 ``from __future__ import annotations``——langgraph 按运行时注解
# 对象识别节点可注入的 ``config`` 形参，字符串化的 ``"RunnableConfig | None"`` 不在其
# 白名单 → config 被静默丢弃（configurable 里的 session/cost/top_k 全落空）+ UserWarning；
# 真注解对象 ``RunnableConfig | None == Optional[RunnableConfig]`` 才匹配（Task 9 实测）。
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agent.prompts import DOCQA_SYSTEM
from app.agent.react_base import run_react_agent
from app.agent.state import AgentState
from app.agent.tools_loader import ToolCallTracker, get_doc_tools
from app.core.config import settings

__all__ = ["docqa_node"]

#: 无引用拒答提示（brief 冻结文案；前导空行与正文隔开）
_NO_CITATION_NOTICE = "\n\n[未找到可引用的文档依据，以上内容请以工具检索结果为准或补充关键词]"


def _safe_writer():
    """同 :mod:`app.agent.nodes` 的同名 helper：无图上下文（如测试直调）→ no-op。"""
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001
        return lambda _chunk: None


async def docqa_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """文档问答节点：hybrid 检索 ReAct + 收尾无引用提示（见模块 docstring）。"""
    tracker = ToolCallTracker()
    await run_react_agent(
        state, config,
        agent_name="docqa",
        tools=get_doc_tools(),
        system_prompt=DOCQA_SYSTEM,
        max_rounds=settings.agent_rounds_doc,
        degrade_label="DocQA",
        tracker=tracker,
    )
    if getattr(tracker, "reacted", False) and not tracker.citations:
        _safe_writer()({"event": "token", "data": {"content": _NO_CITATION_NOTICE}})
    return {}
