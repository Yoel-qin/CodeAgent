"""LangGraph 主图状态（对齐技术栈架构设计 §8）。

运行时上下文（AsyncSession / top_k / agent_type）**不进 state** —— 它们经 RunnableConfig
的 configurable 传入（见 nodes 与 streaming.py），避免把不可序列化/DB 句柄塞进会被
checkpoint 的 state。state 只放可持久化、跨节点流转的数据。
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


def _merge_retrieved(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """retrieved 累加器：按 chunk_id 合并去重，后写覆盖先写。

    多个工具（search_code / get_call_chain / read_code …）可能返回同一 chunk；
    后写覆盖让 read_code 的全文覆盖 search_code 的片段。返回值顺序不保证（按 dict 插入序）。
    """
    merged: dict[str, dict] = {r["chunk_id"]: r for r in (left or [])}
    for r in right or []:
        merged[r["chunk_id"]] = r
    return list(merged.values())


class AgentState(TypedDict, total=False):
    # 输入
    query: str
    conversation_id: str | None
    agent_type: str | None
    # 跨轮会话历史（适配器载入的只读输入；[{role, content}]，最旧→最新）。
    # 不加 reducer：无节点返回它，每轮由 stream_graph 用从 chat_messages 新载入的值覆盖 checkpoint 快照。
    history: list[dict]
    # 多轮消息（add_messages reducer：追加 + 按 id 去重）。
    # 代码理解 Agent（create_react_agent）在其上跑工具调用循环。
    messages: Annotated[list, add_messages]
    # Stage 0 查询理解（query_analysis 节点产出）
    semantic_query: str
    keywords: list[str]
    rewritten: bool
    intent: str  # 意图分类：code | doc | graph | mixed | chitchat（router 据此条件路由）
    # 检索（retrieve 节点产出：pipeline.recall 的 candidates + 漏斗 meta + 引用）
    ranked: list[dict]
    retrieval_meta: dict
    citations: list[dict]
    # 代码理解 Agent 工具累积（工具经 Command 写入；agent_finalize 据此构建引用）
    retrieved: Annotated[list[dict], _merge_retrieved]  # 累积工具返回的候选 chunk（按 chunk_id 去重）
    tool_steps: Annotated[list[dict], operator.add]     # 每步工具调用记录（name/args/摘要）
    # 生成（generate 节点 / agent_finalize 产出）
    context: str
    answer: str
    # HITL 文档维护（M10 中断 + M13 ReAct）：propose 产出 → confirm 中断 → apply/reject 消费。无 reducer，单分支流转。
    proposal: str | None
    decision: dict | None
    stale_anchors: list[dict] | None  # M13：ReAct 可提议多锚点（M10 单 stale_anchor 升级为列表）
    # M35 多 Agent 协作（query_analysis 产、router 读；无 reducer，单分支流转）
    needs_collab: bool
    # WorkingMemory（collab 子图读写；operator.add 累积，跨层传递）
    collab_hypotheses: Annotated[list[dict], operator.add]   # [{hypothesis, confidence, rationale}]
    collab_findings: Annotated[list[dict], operator.add]     # [{chunk_id, finding, hypothesis_id, verdict}]
    collab_suggestions: Annotated[list[dict], operator.add]  # [{suggestion, doc_chunk_id, rationale}]
    # 成本计数器（operator.add 累积，跨层共享；每层读累积值判超限、返回本轮消耗 delta）
    collab_llm_calls: Annotated[int, operator.add]
    collab_tool_calls: Annotated[int, operator.add]
    # M37 领域包：请求期 resolve 的激活包 name（None=无包）；节点经 registry.get(name) 取 pack 对象。
    # 轻量字符串进 state（非整个 pack 对象）——避免 checkpoint 序列化大对象；pack 是启动期加载的进程级常量。
    active_pack_name: str | None
    # M42 QA 缓存：repo 维度键（stream_graph 注入）；命中态经 configurable 上下文传递，不进 checkpoint
    repo_key: str

