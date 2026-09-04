"""ReAct 共享骨架（Plan 3 Task 8）——codenav / docqa 两个场景节点的公共实现。

移植旧库 ``app/agent/agents/_base.py::run_scenario_agent`` 的骨架语义（``_safe_writer`` /
``_merge_callbacks`` / recursion_limit 换算 / 嵌套 astream 事件桥接 / 异常兜底），四处差异：

1. **工具来源**：wrap 后的 MCP ``BaseTool``（:func:`app.agent.tools_loader.wrap_tool` 已带
   计步/循环检测/citation 提取）。Task 5 的 wrap 只写 :class:`ToolCallTracker` 不发事件，
   故骨架在**收尾统一补发**——``tracker.steps`` → ``agent_step`` 事件、``tracker.citations``
   → ``citation`` 事件、``tracker.looped`` → 带 ``loop_detected`` 的 ``agent_step``（循环
   拦截不产生 steps 条目，不发就全链路不可见）。收尾补发 = 事件在 token 之后，顺序不影响消费
   （streaming 层按 event 类型分流）。
2. **降级链**：不再单跑 recall + LLM 生成，而是复用 Task 7 的
   :func:`app.agent.nodes.retrieve_node`（自带 doc 正文增强 + 无 key 片段降级）。
3. **预算**：每请求 :class:`~app.agent.cost.CostController` 从
   ``config["configurable"]["cost"]`` 取（Task 9 streaming 层注入），缺席按 settings 兜底新建。
   ``CostCallbackHandler`` 只记不抛，astream chunk 循环内轮询 ``cost.exceeded`` → raise →
   BudgetExceeded 分支发 notice 即止。
4. **嵌套流**：``stream_mode=["custom", "updates"]``——``custom`` 承接嵌套侧经
   ``get_stream_writer`` 推的事件桥接回主图流；``updates`` 让循环在**每个节点完成后**拿到一个
   chunk（纯 ``custom`` 模式下无人推自定义事件 → 循环体一次都不跑 → 预算轮询沦为死代码）。

异常兜底承诺：任何异常（含 ``GraphRecursionError``——``recursion_limit = max_rounds*2+3`` 触发）
→ 收尾补发 tracker 事件后转 ``retrieve_node`` 降级，**请求永不中断**。
事件即数据：节点返回 ``{}``，answer 由 Task 9 streaming 层从 token 事件累积。
"""
from __future__ import annotations

import warnings

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from loguru import logger

from app.agent.callbacks import CostCallbackHandler, TokenSSEHandler
from app.agent.cost import BudgetExceeded, CostController
from app.agent.nodes import retrieve_node
from app.agent.state import AgentState
from app.agent.tools_loader import ToolCallTracker, wrap_tool
from app.clients.llm import chat_model_for, configured
from app.core.config import settings

__all__ = ["run_react_agent"]


def _safe_writer():
    """主图 custom 流 writer；不在图执行上下文（如测试直调）→ None。沿旧库 ``_base`` 同名 helper。"""
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001
        return None


def _merge_callbacks(existing, handler):
    """把 handler 合并进已有 callbacks（LangGraph 传入的是 CallbackManager）。沿旧库同名 helper。"""
    if existing is None:
        return [handler]
    if isinstance(existing, list):
        return [*existing, handler]
    return [*(getattr(existing, "handlers", []) or []), handler]


def _emit(writer, event: str, data: dict) -> None:
    if writer is not None:
        try:
            writer({"event": event, "data": data})
        except Exception:  # noqa: BLE001 —— 事件发送不破请求
            pass


def _drain_tracker(writer, tracker: ToolCallTracker) -> None:
    """把 tracker 收割结果落成事件：steps → agent_step、citations → citation、looped → agent_step。"""
    for s in tracker.steps:
        _emit(writer, "agent_step", {"tool": s["tool"], "args": s["args"],
                                     "n": s["n"], "duration_ms": s["duration_ms"]})
    for i, name in enumerate(tracker.looped):
        # 循环检测拦截的调用不产生 steps 条目（Task 5 交底）——这里显式落事件，否则循环不可见。
        # duration_ms 恒 None（调用被拦截未执行，无耗时）；键保留 = 冻结事件形状不随路径漂移
        _emit(writer, "agent_step", {"tool": name, "args": {"loop_detected": True},
                                     "n": len(tracker.steps) + i + 1, "duration_ms": None})
    for c in tracker.citations:
        _emit(writer, "citation", c)


async def run_react_agent(state: AgentState, config: RunnableConfig | None, *, agent_name: str,
                          tools: list, system_prompt: str, max_rounds: int, degrade_label: str,
                          tracker: ToolCallTracker | None = None) -> dict:
    """场景 ReAct 节点通用实现：wrap 工具 → 前置 retrieval meta → 跑 Agent → 异常/预算兜底。

    各场景模块以薄节点函数转调本函数（``codenav`` / ``docqa``）。``tracker`` 供节点自建传入
    （docqa 无引用拒答判定要读 ``tracker.citations``）；缺省内建。ReAct 主循环完整跑完时置
    ``tracker.reacted = True``（降级路径不置位）——节点层据此区分「走了 ReAct」与「被降级接管」。
    """
    tracker = tracker if tracker is not None else ToolCallTracker()
    configurable = (config or {}).get("configurable") or {}
    trace = configurable.get("trace")   # M7 SpanCollector；缺席 → 各层零行为变更
    # 会话 repo 机械注入带 repo 参数的工具（Task 10 ④）：系统提示词只"劝" LLM 传 repo，
    # 这里在 wrap 层兜底补缺（LLM 显式传值不被覆盖）——比提示词约束可靠；
    # M9 起连带透传 scopes（configurable["scopes"]，RBAC off → None → wrap 层域防御直通）
    tools = [wrap_tool(t, tracker, default_repo=state.get("repo"), trace=trace,
                       scopes=configurable.get("scopes"))
             for t in tools]
    writer = _safe_writer()

    if not tools:
        # 该侧 MCP server 挂（降级链：code-mcp 挂 → 仅文档问答的另一侧）
        _emit(writer, "token", {"content": f"[{degrade_label}: 工具服务不可用，转为检索回答]"})
        await retrieve_node(state, config)
        return {}
    if not configured():
        # 无 LLM key：不进 ReAct，直接检索兜底（retrieve_node 自带无 key 片段降级）
        await retrieve_node(state, config)
        return {}

    _emit(writer, "retrieval", {
        "mode": agent_name,
        "intent": state.get("intent", ""),
        "confidence": state.get("confidence", 0.0),
        "tools": [t.name for t in tools],
        "code_hits": 0,
        "doc_hits": 0,
    })

    configurable = (config or {}).get("configurable") or {}
    cost = configurable.get("cost") or CostController(
        max_tokens=settings.cost_max_tokens, max_llm_calls=settings.cost_max_llm_calls)

    # M7 agent span：只包 ReAct 主循环段（前置降级分支没跑 Agent，不产生 agent span）；
    # 正常收尾 attrs 补 reacted；BudgetExceeded/异常降级 → status="error"（error=budget/cause）
    agent_sid = trace.start("agent", agent_name,
                            attrs={"tools": [t.name for t in tools]}) if trace is not None else None

    cfg = dict(config or {})
    cfg["callbacks"] = _merge_callbacks(cfg.get("callbacks"), TokenSSEHandler(writer))
    cfg["callbacks"] = _merge_callbacks(cfg["callbacks"], CostCallbackHandler(cost, trace=trace))
    # Agent 每轮 = model + tools 两步；recursion_limit 兜住超限（触发 GraphRecursionError → 降级）
    cfg["recursion_limit"] = max_rounds * 2 + 3

    try:
        with warnings.catch_warnings():
            # langgraph-prebuilt 的 create_react_agent 在 v1 标记弃用（迁 langchain.agents），
            # 功能完整，抑制告警保持日志干净（旧库同款处理）。
            warnings.simplefilter("ignore")
            # repo 直达工具参数：conversation 的 repo 只在图 state 里、工具入参由 LLM 产出，
            # MCP 工具 repo 缺省回落 default_repo（M4 验收实测：sa-token 会话里 docqa 检索
            # 的是 rocketmq 的空文档库 → 恒 0 命中）——把当前仓库追加进系统提示词强制透传
            repo = state.get("repo") or settings.default_repo
            agent = create_react_agent(
                model=chat_model_for("reasoning"), tools=tools,
                prompt=(f"{system_prompt}\n\n当前仓库 repo={repo}，"
                        "调用工具时 repo 参数一律传这个值。"))
        seed = [*(state.get("history") or []), {"role": "user", "content": state["query"]}]
        async for stream, chunk in agent.astream({"messages": seed}, config=cfg,
                                                 stream_mode=["custom", "updates"]):
            if stream == "custom" and isinstance(chunk, dict) and writer is not None:
                # 嵌套侧经 get_stream_writer 推的事件桥接回主图流
                writer(chunk)
            # 预算超限 → 中断 Agent 循环进降级（回调里抛不出去，只能在此拦）
            if cost.exceeded is not None:
                raise cost.exceeded
        tracker.reacted = True   # ReAct 主循环完整跑完（docqa 无引用拒答判定用）
    except BudgetExceeded as e:
        # 部分结果即止：已发生的 agent_step/citation 先补发，再以模板 notice 顶替生成
        if trace is not None:
            trace.end(agent_sid, status="error", error="budget")
        _drain_tracker(writer, tracker)
        _emit(writer, "token", {"content": e.notice()})
        return {}
    except Exception as e:  # noqa: BLE001 —— 含 GraphRecursionError，兜底降级，请求永不中断
        cause = "recursion_limit" if isinstance(e, GraphRecursionError) else type(e).__name__
        logger.warning("react_base[{}]: ReAct 失败（{}），降级 retrieve: {}", agent_name, cause, e)
        if trace is not None:
            # 先关 agent span 再降级——retrieve 的 retrieval span 不计入本 agent 耗时
            trace.end(agent_sid, status="error", error=cause)
        _drain_tracker(writer, tracker)
        await retrieve_node(state, config)
        return {}
    _drain_tracker(writer, tracker)
    if trace is not None:
        trace.end(agent_sid, attrs={"reacted": tracker.reacted})
    return {}
