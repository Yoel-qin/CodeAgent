"""场景 Agent 共享骨架（Phase 7 Milestone 4 抽取）。

``code_understand`` / ``doc_answer`` / ``change_impact`` 三个自动 Agent
（LangGraph 预置 ``create_react_agent``：LLM 自主选工具、循环到能作答）的主图节点逻辑同构：
  1. 先推一条 agent 风格的 ``retrieval`` meta 事件；
  2. 注入 ``TokenSSEHandler`` 回调（作答轮逐 token 推 SSE）后跑 Agent；
  3. try/except 兜底：Agent 异常/超限 → ``_degrade``（单跑 recall + 流式作答），**永不中断请求**。

三个 Agent 仅 5 处差异：``agent_name``（meta 标签）/ ``tools`` / ``prompt`` / ``build_agent`` 工厂 /
``degrade_label``（降级文案），据此参数化为 ``run_scenario_agent``。各 Agent 模块只保留
prompt + 惰性单例工厂 + 薄节点函数（转调本模块）。

工具侧（见 ``tools/*_tools.py``）经 ``get_stream_writer`` 推 ``agent_step`` + 逐条 ``citation``，
故引用由适配器从事件累积，无需 Command / 图 state 写入。
M41：collector 含 trace 时用 TraceCallbackHandler 替换 TokenSSEHandler；
agent span 包嵌套 astream；_degrade 加 degrade span。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import nullcontext

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.cost import BudgetExceeded
from app.agent.llm import CostCallbackHandler, TokenSSEHandler, TraceCallbackHandler, configured
from app.agent.state import AgentState
from app.clients.llm_client import llm
from app.core.config import settings
from app.retrieval.pipeline import pipeline
from app.services.chat_service import (
    _citation,
    _enrich_content_types,
    _no_key_notice,
    build_context,
    build_messages,
)


def _safe_writer():
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001
        return None


def _merge_callbacks(existing, handler):
    """把 TokenSSEHandler 合并进已有 callbacks（LangGraph 传入的是 AsyncCallbackManager）。"""
    if existing is None:
        return [handler]
    if isinstance(existing, list):
        return [*existing, handler]
    return [*(getattr(existing, "handlers", []) or []), handler]


def _emit_retrieval_meta(state: AgentState, *, agent_name: str, tools: list) -> None:
    if (w := _safe_writer()) is None:
        return
    meta = {
        "mode": "agent",
        "agent": agent_name,
        "tools": [t.name for t in tools],
        "terms": state.get("keywords", []),
        "recall": {"vector": 0, "lexical": 0, "graph": 0},
        "merged": 0,
        "coarse": None,
        "fine": 0,
        "rerank_on": False,
        "rewritten": state.get("rewritten", False),
        "embedding_strategy": settings.embedding_strategy,
    }
    try:
        w({"event": "retrieval", "data": meta})
    except Exception:  # noqa: BLE001
        pass


async def _degrade(state: AgentState, config: RunnableConfig, err: Exception | None,
                   *, degrade_label: str) -> None:
    """Agent 不可用/异常/超限时兜底：单跑 recall + 流式作答（复用 legacy 检索+生成逻辑）。
    M41：collector 存在时记 degrade span。
    """
    collector = config["configurable"].get("trace")
    t0 = time.perf_counter()
    session: AsyncSession = config["configurable"]["session"]
    top_k = config["configurable"].get("top_k", 8)
    allowed_kinds = config["configurable"].get("allowed_kinds")   # M45
    query = state["query"]
    agent_type = config["configurable"].get("agent_type")
    writer = _safe_writer()
    try:
        ranked, meta = await pipeline.recall(session, query, top_k=top_k,
                                             allowed_kinds=allowed_kinds)
        await _enrich_content_types(session, ranked)
        if writer:
            writer({"event": "retrieval", "data": meta})
            for r in ranked:
                writer({"event": "citation", "data": _citation(r)})
        context = build_context(ranked)
        if isinstance(err, BudgetExceeded):
            # M42：预算超限降级——不再烧 LLM，模板 notice 逐 token 顶替生成
            if writer:
                writer({"event": "token", "data": {"content": err.notice()}})
        elif llm.configured:
            async for tok in llm.stream_tokens(
                build_messages(query, context, agent_type, state.get("history", []))
            ):
                if writer:
                    writer({"event": "token", "data": {"content": tok}})
        elif writer:
            writer({"event": "token", "data": {"content": _no_key_notice(meta)}})
    except Exception as e:  # noqa: BLE001
        # 兜底的兜底：确保至少有一个 token 事件，不中断请求
        if writer:
            msg = f"[{degrade_label} Agent 降级失败：{type(e).__name__}]"
            if err:
                msg = f"[Agent 异常({type(err).__name__})且兜底失败]"
            writer({"event": "token", "data": {"content": msg}})
    finally:
        if collector is not None:
            collector.record("degrade", degrade_label,
                             (time.perf_counter() - t0) * 1000,
                             parent_id=collector.stack_top,
                             attrs={"cause": (err.reason if isinstance(err, BudgetExceeded)
                                              else (type(err).__name__ if err else "no_key"))})


async def run_scenario_agent(
    state: AgentState,
    config: RunnableConfig,
    *,
    agent_name: str,
    tools: list,
    build_agent: Callable[[], object],
    degrade_label: str,
) -> dict:
    """场景 Agent 节点通用实现：前置 retrieval meta、注入 token 回调、跑 Agent、异常兜底。

    各 Agent 模块以薄节点函数转调本函数（见 ``code_understand`` / ``doc_answer`` / ``change_impact``）。
    M41：collector 存在时用 TraceCallbackHandler 替换 TokenSSEHandler；agent span 包嵌套 astream。
    """
    collector = config["configurable"].get("trace")
    cost = config["configurable"].get("cost")   # M42：预算控制器（off → None 零开销）
    _emit_retrieval_meta(state, agent_name=agent_name, tools=tools)
    if not configured():
        # 无 LLM key：不进 Agent，直接兜底（retrieve 路径自身也会降级，这里统一兜底作答）
        await _degrade(state, config, None, degrade_label=degrade_label)
        return {}
    cfg = dict(config)
    base_handler = (TraceCallbackHandler(collector, emit_tokens=True)
                    if collector is not None else TokenSSEHandler())
    cfg["callbacks"] = _merge_callbacks(config.get("callbacks"), base_handler)
    if cost is not None:
        cfg["callbacks"] = _merge_callbacks(cfg["callbacks"], CostCallbackHandler(cost))
    # Agent 每轮 = agent + tools 两步；recursion_limit 兜住超限（触发 GraphRecursionError → 兜底）
    cfg["recursion_limit"] = settings.agent_max_iterations * 2 + 3
    parent_writer = _safe_writer()  # 主图 custom 流；Agent 是手动嵌套调用，需把其 custom 事件桥接上来
    try:
        agent = build_agent()
        # 跨轮记忆：把会话历史前置进消息种子（最旧→最新 + 当前 query），让 ReAct 循环携带上下文。
        seed = [*state.get("history", []), {"role": "user", "content": state["query"]}]
        cm = (collector.span("agent", agent_name, tools=[t.name for t in tools])
              if collector is not None else nullcontext())
        with cm:
            async for chunk in agent.astream(
                {"messages": seed},
                config=cfg,
                stream_mode="custom",
            ):
                # Agent 内工具/回调经 get_stream_writer 推到【嵌套】custom 流；此处转发到主图流
                if parent_writer and isinstance(chunk, dict):
                    parent_writer(chunk)
                # M42：预算超限 → 中断 Agent 循环进 _degrade（回调里抛不出去，只能在此拦）
                if cost is not None and cost.exceeded is not None:
                    raise cost.exceeded
    except Exception as e:  # noqa: BLE001
        await _degrade(state, config, e, degrade_label=degrade_label)
    return {}
