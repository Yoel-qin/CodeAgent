"""Agent 层共享图状态（Plan 3 Task 6）——Task 9 图装配与所有节点的状态契约。

plain ``TypedDict``（无需 total 语义）：LangGraph 节点返回 dict 做部分更新写回，
未写键保持原值。仅放可序列化的跨节点数据；per-request 资源（session / tracker /
工具缓存）走 ``RunnableConfig.configurable``，**不进 checkpoint 状态**（沿旧库
M35 契约）。

字段分两组：

- **输入**（入口侧注入）：``query`` / ``repo`` / ``conversation_id`` / ``history``
- **Router 写回**（Task 6 :mod:`app.agent.query_analysis`）：``intent`` /
  ``confidence`` / ``route``——Task 9 的 conditional edge 只读 ``state["route"]``
"""
from __future__ import annotations

from typing import TypedDict

__all__ = ["AgentState"]


class AgentState(TypedDict):
    """图状态：入口输入 + Router 写回（后续节点按需追加键，新增键不破坏既有节点）。"""

    query: str
    repo: str
    conversation_id: str
    history: list[dict]
    intent: str
    confidence: float
    route: str
