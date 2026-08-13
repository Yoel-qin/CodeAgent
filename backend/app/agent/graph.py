"""编译主图：Orchestrator 意图分发表 + 多场景自动 Agent（Milestone 2/3/4）。

拓扑：
  START → query_analysis（改写 + 意图分类）
        → router（条件边，意图→Agent 分发表）
            ├─ code_understand（create_react_agent 自动 Agent，代码工具）
            ├─ doc_answer      （create_react_agent 自动 Agent，文档工具）
            ├─ change_impact   （create_react_agent 自动 Agent，变更影响/调用图工具）
            ├─ bug_diagnosis   （create_react_agent 自动 Agent，缺陷诊断/回归排查工具）
            ├─ code_review     （create_react_agent 自动 Agent，代码审查/度量工具）
            ├─ test_generation （create_react_agent 自动 Agent，测试生成/JUnit 工具）
            ├─ web_search      （create_react_agent 自动 Agent，联网 MCP 工具，库外问题）
            ├─ collab（M35：多 Agent 协作子图 diagnose→verify→refine，opt-in 且 needs_collab
            │        时切；collab_node wrapper 手动 astream 子图 + 桥接 custom 事件到父流 +
            │        整体 try/except→_degrade 降级伞，请求永不中断；retrieval_meta.mode="collab"）
            ├─ propose→confirm→apply|reject（HITL 文档维护，opt-in 显式 DOC_MAINTAIN；
            │                              M13：propose 已是 ReAct Agent，interrupt() 仍在主图 confirm 节点）
            └─ retrieve → generate        （兜底：现有 3 路→rerank→生成）
        → post_process → END

各支路都产出 retrieval / citation / token SSE 事件（契约同 legacy stream_chat），
适配器据此累积并落库。惰性单例：首次 get_graph() 时编译（含 MemorySaver checkpointer）。
"""
from __future__ import annotations

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agent.agents._base import _degrade
from app.agent.agents.bug_diagnosis import bug_diagnosis
from app.agent.agents.change_impact import change_impact
from app.agent.agents.code_review import code_review
from app.agent.agents.code_understand import code_understand
from app.agent.agents.doc_answer import doc_answer
from app.agent.agents.test_generation import test_generation
from app.agent.agents.web import web_search
from app.agent.collab.subgraph import build_collab_subgraph
from app.agent.memory.checkpointer import get_checkpointer
from app.agent.nodes.doc_maintain import (
    after_confirm,
    after_propose,
    apply_stale,
    confirm,
    propose,
    reject,
)
from app.agent.nodes.generate import generate
from app.agent.nodes.post_process import post_process
from app.agent.nodes.query_analysis import query_analysis
from app.agent.nodes.retrieve import retrieve
from app.agent.nodes.router import route
from app.agent.state import AgentState

_graph_app = None
_collab_subgraph = None  # M35：协作子图惰性单例（首次 collab_node 调用时编译）


def _safe_writer():
    """主图 custom 流 writer；非 LangGraph 运行上下文（如单测）下返回 None。"""
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001
        return None


async def collab_node(state, config) -> dict:
    """M35 多 Agent 协作主图节点：包裹协作子图执行 + 事件桥接 + 整体降级伞。

    - **事件桥接（I-5）**：手动 ``astream(subgraph, stream_mode="custom")`` 消费子图节点
      经 ``get_stream_writer`` 推的 ``agent_step``/``citation``/``token``/``retrieval`` 事件，
      转发到父图 custom 流（与 ``_base.run_scenario_agent`` 同款嵌套转发模式）。
    - **降级伞（I-1）**：子图任一未兜住的异常 → 复用 ``_base._degrade``（单跑
      ``pipeline.recall`` + 流式作答），请求永不中断（spec §7.2）。
    - **return {}**：``collab_*`` WorkingMemory 字段仅在子图内部流转，主图后续 ``post_process``
      透传，无需把子图 state delta 带回主图。
    """
    global _collab_subgraph
    parent_writer = _safe_writer()
    if _collab_subgraph is None:
        _collab_subgraph = build_collab_subgraph()
    try:
        async for chunk in _collab_subgraph.astream(state, config=config, stream_mode="custom"):
            if parent_writer and isinstance(chunk, dict):
                parent_writer(chunk)
    except Exception as e:  # noqa: BLE001  I-1: 子图整体降级伞
        await _degrade(state, config, e, degrade_label="多 Agent 协作")
    return {}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("query_analysis", query_analysis)
    graph.add_node("code_understand", code_understand)
    graph.add_node("doc_answer", doc_answer)
    graph.add_node("change_impact", change_impact)
    graph.add_node("bug_diagnosis", bug_diagnosis)
    graph.add_node("code_review", code_review)
    graph.add_node("test_generation", test_generation)
    graph.add_node("web_search", web_search)
    graph.add_node("collab", collab_node)  # M35：协作 wrapper（子图 + 事件桥接 + 降级伞；opt-in）
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("post_process", post_process)
    graph.add_node("propose", propose)          # HITL（M10）：propose→confirm→apply|reject
    graph.add_node("confirm", confirm)
    graph.add_node("apply", apply_stale)
    graph.add_node("reject", reject)
    graph.add_edge(START, "query_analysis")
    graph.add_conditional_edges(
        "query_analysis", route,
        {"code_understand": "code_understand", "doc_answer": "doc_answer",
         "change_impact": "change_impact", "bug_diagnosis": "bug_diagnosis",
         "code_review": "code_review", "test_generation": "test_generation",
         "web_search": "web_search",
         "collab": "collab",
         "retrieve": "retrieve", "propose": "propose"},
    )
    graph.add_conditional_edges(
        "propose", after_propose, {"confirm": "confirm", "post_process": "post_process"},
    )
    graph.add_conditional_edges(
        "confirm", after_confirm, {"apply": "apply", "reject": "reject"},
    )
    graph.add_edge("apply", "post_process")
    graph.add_edge("reject", "post_process")
    graph.add_edge("code_understand", "post_process")
    graph.add_edge("doc_answer", "post_process")
    graph.add_edge("change_impact", "post_process")
    graph.add_edge("bug_diagnosis", "post_process")
    graph.add_edge("code_review", "post_process")
    graph.add_edge("test_generation", "post_process")
    graph.add_edge("web_search", "post_process")
    graph.add_edge("collab", "post_process")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "post_process")
    graph.add_edge("post_process", END)
    return graph.compile(checkpointer=get_checkpointer())


def get_graph():
    """惰性单例：避免每次请求重建图。"""
    global _graph_app
    if _graph_app is None:
        _graph_app = build_graph()
    return _graph_app
