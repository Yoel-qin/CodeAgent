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

from app.agent.llm import configured, get_chat_model
from app.core.config import settings


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


# ---- 三层协作节点（diagnose / verify / refine） + 共享骨架 ----

from app.agent.collab import budget, memory  # noqa: E402, I001
from app.agent.collab.prompts import DIAGNOSE_PROMPT, REFINE_PROMPT, VERIFY_PROMPT  # noqa: E402
from app.agent.tools.code_tools import (  # noqa: E402
    get_call_chain, get_callers, get_recent_changes, get_related_docs, read_code, search_code,
)
from app.agent.tools.doc_tools import search_docs  # noqa: E402

_DIAGNOSE_TOOLS = [search_code, get_call_chain]
_VERIFY_TOOLS = [read_code, get_callers, get_recent_changes]
_REFINE_TOOLS = [search_docs, get_related_docs]


def _layer_input(state: dict, layer: str) -> str:
    """构造每层喂给 _bounded_tool_loop 的 user 文本（携带 WorkingMemory 上下文）。"""
    q = state.get("query", "")
    if layer == "diagnose":
        return q
    if layer == "verify":
        hs = state.get("collab_hypotheses") or []
        idx = "\n".join(f"{i}. {h.get('hypothesis')}" for i, h in enumerate(hs)) or "（无假设）"
        return f"原始问题：{q}\n待验证假设：\n{idx}"
    # refine
    hs = state.get("collab_hypotheses") or []
    fs = state.get("collab_findings") or []
    htxt = "\n".join(f"- {h.get('hypothesis')}" for h in hs) or "（无）"
    ftxt = "\n".join(f"- [{f.get('verdict')}] {f.get('finding')}" for f in fs) or "（无）"
    return f"原始问题：{q}\n假设：\n{htxt}\n验证结论：\n{ftxt}"


async def _extract(schema, prompt: str, observations: str):
    """一次 with_structured_output 提取（预算允许时）；失败→None。"""
    try:
        structured = get_chat_model().with_structured_output(schema)
        return await structured.ainvoke([
            {"role": "system", "content": prompt},
            {"role": "user", "content": observations or "（无检索观察）"},
        ])
    except Exception:  # noqa: BLE001
        return None


async def _run_layer(state, config, *, layer: str, prompt: str, tools: list, schema) -> dict:
    """三层共性：_bounded_tool_loop（检索）+ 结构化提取。返回 state delta。

    预算：从 state 读已消耗数算余量；extract 的 1 次 LLM 调用也计入 collab_llm_calls。
    """
    if not configured():
        return {}
    used_l = int(state.get("collab_llm_calls", 0))
    used_t = int(state.get("collab_tool_calls", 0))
    loop_res = await _bounded_tool_loop(
        system_prompt=prompt, user_prompt=_layer_input(state, layer), tools=tools,
        max_rounds=settings.collab_max_rounds_per_layer,
        llm_budget_left=budget.remaining(used_l, settings.collab_max_llm_calls),
        tool_budget_left=budget.remaining(used_t, settings.collab_max_tool_calls),
        layer_name=f"collab.{layer}", config=config)
    out: dict = {
        "tool_steps": loop_res["tool_steps"],
        "collab_llm_calls": loop_res["collab_llm_calls"],
        "collab_tool_calls": loop_res["collab_tool_calls"],
    }
    if budget.remaining(used_l + loop_res["collab_llm_calls"], settings.collab_max_llm_calls) > 0:
        extracted = await _extract(schema, prompt, loop_res["observations"])
        if extracted is not None:
            out["collab_llm_calls"] = loop_res["collab_llm_calls"] + 1
            if schema is memory.HypothesisList:
                out["collab_hypotheses"] = [h.model_dump() for h in extracted.hypotheses]
            elif schema is memory.FindingList:
                out["collab_findings"] = [f.model_dump() for f in extracted.findings]
            elif schema is memory.SuggestionList:
                out["collab_suggestions"] = [s.model_dump() for s in extracted.suggestions]
                _emit_report_token(state, extracted)
    return out


def _emit_report_token(state: dict, suggestions) -> None:
    """refine 层：把诊断报告作为 token 事件流出（用户在 SSE 流看到完整报告）。"""
    if (w := _safe_writer()) is None:
        return
    report = budget.build_collab_report(
        state.get("collab_hypotheses") or [],
        state.get("collab_findings") or [],
        [s.model_dump() for s in suggestions.suggestions],
    )
    try:
        w({"event": "token", "data": {"content": report}})
    except Exception:  # noqa: BLE001
        pass


async def diagnose(state, config) -> dict:
    """诊断假设层。"""
    return await _run_layer(state, config, layer="diagnose", prompt=DIAGNOSE_PROMPT,
                            tools=_DIAGNOSE_TOOLS, schema=memory.HypothesisList)


async def verify(state, config) -> dict:
    """代码验证层。"""
    return await _run_layer(state, config, layer="verify", prompt=VERIFY_PROMPT,
                            tools=_VERIFY_TOOLS, schema=memory.FindingList)


async def refine(state, config) -> dict:
    """文档调优层（+ 流式诊断报告 token）。"""
    return await _run_layer(state, config, layer="refine", prompt=REFINE_PROMPT,
                            tools=_REFINE_TOOLS, schema=memory.SuggestionList)
