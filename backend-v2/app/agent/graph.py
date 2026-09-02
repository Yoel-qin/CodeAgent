"""主图装配（Plan 3 Task 9）：StateGraph(AgentState) 单层图，编译一次模块级复用。

拓扑（brief 冻结）：``query_analysis``（Task 6 router）入口 → conditional edges 只读
``state["route"]``（``decide_route`` 真值表产出，取值仅四种）映射四节点 → 各节点
``→ END``。**无 checkpointer**——跨轮记忆走 ``chat_messages`` history（Task 9
streaming 层注入 ``state["history"]``），spec M4 无跨重启恢复要求。

spec 偏差（写进 :mod:`app.agent.streaming` docstring）：spec §5.1 的独立
``post_process → END`` 节点不落图——其后置职责（answer/citations 聚合 + 持久化 +
done 事件）折入 streaming 适配层（事件即数据，节点只发事件不写 state）。

主图自身是**单层图**：各节点经 ``get_stream_writer()`` 推的事件以 ``stream_mode="custom"``
从 :func:`GRAPH.astream` 直接流出（与 Task 8 嵌套 agent 的桥接不同，无需二次转发）。
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agent.codenav import codenav_node
from app.agent.docqa import docqa_node
from app.agent.nodes import clarify_node, retrieve_node
from app.agent.query_analysis import query_analysis_node
from app.agent.state import AgentState

__all__ = ["GRAPH", "build_graph"]

#: conditional edges 的 path_map：``state["route"]`` → 节点名（decide_route 的取值域）
_ROUTES = {"codenav": "codenav", "docqa": "docqa", "retrieve": "retrieve", "clarify": "clarify"}


def build_graph():
    """装配主图（编译一次；测试可用本函数重建隔离实例）。"""
    graph = StateGraph(AgentState)
    graph.add_node("query_analysis", query_analysis_node)
    graph.add_node("codenav", codenav_node)
    graph.add_node("docqa", docqa_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("clarify", clarify_node)
    graph.set_entry_point("query_analysis")
    graph.add_conditional_edges("query_analysis", lambda state: state.get("route", "retrieve"), _ROUTES)
    for name in _ROUTES.values():
        graph.add_edge(name, END)
    return graph.compile()


GRAPH = build_graph()
