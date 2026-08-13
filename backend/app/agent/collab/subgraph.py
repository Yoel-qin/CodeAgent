"""M35 协作子图：diagnose → verify → refine（共享 AgentState，不加 checkpointer）。

作为主图节点 ``collab`` 挂入（graph.py）。子图共享主图 checkpoint（其 state 是主图
state 一部分，已被主图 checkpointer 持久化），故编译时不加 checkpointer（避免双重）。
custom 事件经 get_stream_writer 桥接到父流（同 run_scenario_agent 模式）。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.collab.nodes import diagnose, refine, verify
from app.agent.state import AgentState


def build_collab_subgraph():
    sg = StateGraph(AgentState)
    sg.add_node("diagnose", diagnose)
    sg.add_node("verify", verify)
    sg.add_node("refine", refine)
    sg.add_edge(START, "diagnose")
    sg.add_edge("diagnose", "verify")
    sg.add_edge("verify", "refine")
    sg.add_edge("refine", END)
    return sg.compile()  # 不加 checkpointer
