"""M35 协作三层轻量节点 + 有界 tool-calling helper。

每层节点 = 一次有导向 LLM 调用 + 该层工具子集的**手动有界 tool-calling 循环**
（``_bounded_tool_loop``），**非** ``create_react_agent`` 自主循环。节点读 WorkingMemory
（``collab_*`` 字段）、返回 state delta 写下层。工具经 ``@tool`` 对象 ``.ainvoke`` 执行
（复用其发 ``citation``）；``agent_step`` 的「层」标签由本模块 ``_emit_agent_step`` 推
（``agent: "collab.<层>"``），与工具内部 ``_emit_step`` 并存。

成本走 state 计数器（``collab_llm_calls`` / ``collab_tool_calls``，operator.add）。
"""
from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.agent.llm import get_chat_model


def _safe_writer():
    try:
        from langgraph.config import get_stream_writer
        return get_stream_writer()
    except Exception:  # noqa: BLE001
        return None


def _emit_agent_step(layer_name: str, tool: str, args: dict) -> None:
    """推一条带「层」标签的 agent_step（collab.<层>），区别于普通场景 Agent。"""
    if (w := _safe_writer()) is None:
        return
    try:
        w({"event": "agent_step", "data": {"agent": layer_name, "tool": tool, "args": args}})
    except Exception:  # noqa: BLE001
        pass


async def _bounded_tool_loop(*, system_prompt: str, user_prompt: str, tools: list,
                             max_rounds: int, llm_budget_left: int, tool_budget_left: int,
                             layer_name: str, config: RunnableConfig) -> dict:
    """手动有界 tool-calling：每轮 LLM.ainvoke(bind_tools)；无 tool_calls 即止；
    达 max_rounds / 预算耗尽即止。一轮多个 tool_calls → asyncio.gather 并行（=「同层并行」）。

    返回 ``{tool_steps, observations, collab_llm_calls, collab_tool_calls}``（state delta 片段）。
    工具经 ``@tool`` 对象 ``.ainvoke(args, config)`` —— 复用其发 citation 的逻辑。
    """
    model = get_chat_model().bind_tools(tools)
    tool_by_name = {t.name: t for t in tools}
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    tool_steps: list[dict] = []
    observations: list[str] = []
    llm_used = 0
    tool_used = 0
    for _ in range(max_rounds):
        if llm_budget_left - llm_used <= 0:
            break
        resp = await model.ainvoke(messages)
        llm_used += 1
        messages.append(resp)
        calls = list(getattr(resp, "tool_calls", None) or [])
        if not calls:
            break
        afford = tool_budget_left - tool_used
        if afford <= 0:
            break
        run_calls = calls if len(calls) <= afford else calls[:afford]

        async def _exec(tc):  # 同层并行
            obs = await tool_by_name[tc["name"]].ainvoke(tc.get("args", {}), config=config)
            return tc, obs

        results = await asyncio.gather(*[_exec(c) for c in run_calls])
        for tc, obs in results:
            tool_used += 1
            args = tc.get("args", {})
            tool_steps.append({"agent": layer_name, "tool": tc["name"], "args": args})
            observations.append(f"[{tc['name']}] {obs}")
            _emit_agent_step(layer_name, tc["name"], args)
            messages.append(ToolMessage(content=str(obs), tool_call_id=tc.get("id") or tc["name"]))
        if len(calls) > afford:
            break  # 工具预算耗尽，停当前层
    return {"tool_steps": tool_steps, "observations": "\n\n".join(observations),
            "collab_llm_calls": llm_used, "collab_tool_calls": tool_used}
