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
            ├─ propose→confirm→apply|reject（HITL 文档维护，opt-in 显式 DOC_MAINTAIN；
            │                              M13：propose 已是 ReAct Agent，interrupt() 仍在主图 confirm 节点）
            └─ retrieve → generate        （兜底：现有 3 路→rerank→生成）
        → post_process → END

各支路都产出 retrieval / citation / token SSE 事件（契约同 legacy stream_chat），
适配器据此累积并落库。惰性单例：首次 get_graph() 时编译（含 MemorySaver checkpointer）。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.agents.bug_diagnosis import bug_diagnosis
from app.agent.agents.change_impact import change_impact
from app.agent.agents.code_review import code_review
from app.agent.agents.code_understand import code_understand
from app.agent.agents.doc_answer import doc_answer
from app.agent.agents.test_generation import test_generation
from app.agent.agents.web import web_search
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
