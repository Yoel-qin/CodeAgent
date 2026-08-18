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
from contextlib import nullcontext

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.agent.agents._base import _merge_callbacks
from app.agent.llm import CostCallbackHandler, TraceCallbackHandler, get_chat_model, model_for
from app.clients.model_router import endpoint_for
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
                             layer_name: str, config: RunnableConfig,
                             llm_config: dict | None = None) -> dict:
    """手动有界 tool-calling：每轮 LLM.ainvoke(bind_tools)；无 tool_calls 即止；
    达 max_rounds / 预算耗尽即止。一轮多个 tool_calls → asyncio.gather 并行（=「同层并行」）。

    返回 ``{tool_steps, observations, collab_llm_calls, collab_tool_calls}``（state delta 片段）。
    工具经 ``@tool`` 对象 ``.ainvoke(args, config)`` —— 复用其发 citation 的逻辑。
    M41：llm_config 由调用方构建（含 TraceCallbackHandler），传给 model.ainvoke 记 llm span。
    """
    model = get_chat_model().bind_tools(tools)
    tool_by_name = {t.name: t for t in tools}
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    tool_steps: list[dict] = []
    observations: list[str] = []
    llm_used = 0
    tool_used = 0
    # M41：llm_config 由调用方注入（含 TraceCallbackHandler）；未提供则用原始 config
    invoke_config = llm_config if llm_config is not None else (config if isinstance(config, dict) else {})
    cost = ((config or {}).get("configurable", {}).get("cost")
            if isinstance(config, dict) else None)
    for _ in range(max_rounds):
        if cost is not None:
            cost.check()   # M42：预算超限 → BudgetExceeded → _run_layer I-1 catch 优雅停
        if llm_budget_left - llm_used <= 0:
            break
        resp = await model.ainvoke(messages, config=invoke_config)
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
            tool = tool_by_name.get(tc["name"])  # I-3: 防工具名幻觉（None→跳过该工具）
            if tool is None:
                return tc, None
            obs = await tool.ainvoke(tc.get("args", {}), config=config)
            return tc, obs

        # I-3: return_exceptions=True —— 单工具异常记 step、不炸层（spec §7.2）
        results = await asyncio.gather(*[_exec(c) for c in run_calls], return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                _emit_agent_step(layer_name, "(error)", {"error": type(r).__name__})
                continue
            tc, obs = r
            if obs is None:
                # 工具名幻觉（schema 外 name）→ 记 step、跳过该工具，不 KeyError
                _emit_agent_step(layer_name, "(unknown_tool)", {"name": tc["name"]})
                continue
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


async def _extract(schema, prompt: str, observations: str, *,
                   llm_config: dict | None = None):
    """一次 with_structured_output 提取（预算允许时）；失败→None。

    M41：llm_config 由调用方构建（含 TraceCallbackHandler），传给 ainvoke 记 llm span。
    """
    try:
        structured = model_for("extraction").with_structured_output(schema)
        kw = {"config": llm_config} if llm_config is not None else {}
        return await structured.ainvoke([
            {"role": "system", "content": prompt},
            {"role": "user", "content": observations or "（无检索观察）"},
        ], **kw)
    except Exception:  # noqa: BLE001
        return None


async def _run_layer(state, config, *, layer: str, prompt: str, tools: list, schema) -> dict:
    """三层共性：_bounded_tool_loop（检索）+ 结构化提取。返回 state delta。

    预算：从 state 读已消耗数算余量；extract 的 1 次 LLM 调用也计入 collab_llm_calls。

    I-1（per-layer try/except）：单层 LLM/工具异常 → catch、记一条 step、返回空 delta，
    用已有 WorkingMemory 继续下层（不炸整个子图）。
    I-2（refine 优雅收尾）：refine 层末尾无论 extract 成功与否都 emit 报告 token（防空响应）。
    I-4（retrieval meta）：refine 层末尾 emit 一条 mode="collab" retrieval 指标事件。
    M41：collector 存在时包 agent span（kind="agent"，name=层名）；collector=None → nullcontext 零开销。
    """
    collector = (config or {}).get("configurable", {}).get("trace") if isinstance(config, dict) else None
    cost = (config or {}).get("configurable", {}).get("cost") if isinstance(config, dict) else None
    # M41/M42：构造带 Trace/Cost 回调的 llm_config（均 None → None，下层用原始 config）
    llm_config = None
    cbs: list = []
    if collector is not None:
        cbs.append(TraceCallbackHandler(collector))
    if cost is not None:
        cbs.append(CostCallbackHandler(cost))
    if cbs:
        base = config if isinstance(config, dict) else {}
        llm_config = dict(base)
        merged = llm_config.get("callbacks")
        for cb in cbs:
            merged = _merge_callbacks(merged, cb)
        llm_config["callbacks"] = merged
    with (collector.span("agent", layer) if collector is not None else nullcontext()):
        if not bool(endpoint_for("extraction").api_key):
            # 无 extraction 档 key：refine 层仍兜底汇总（用已累积 WM），避免空响应
            if schema is memory.SuggestionList:
                _emit_report_token(state, None)
                _emit_collab_retrieval_meta(state, 0, 0, 0)
            return {}
        used_l = int(state.get("collab_llm_calls", 0))
        used_t = int(state.get("collab_tool_calls", 0))
        try:
            loop_res = await _bounded_tool_loop(
                system_prompt=prompt, user_prompt=_layer_input(state, layer), tools=tools,
                max_rounds=settings.collab_max_rounds_per_layer,
                llm_budget_left=budget.remaining(used_l, settings.collab_max_llm_calls),
                tool_budget_left=budget.remaining(used_t, settings.collab_max_tool_calls),
                layer_name=f"collab.{layer}", config=config, llm_config=llm_config)
        except Exception as e:  # noqa: BLE001  I-1: 单层异常 → 跳过、用已有 WM 继续
            _emit_agent_step(f"collab.{layer}", "(layer_error)",
                             {"error": type(e).__name__, "msg": str(e)})
            if schema is memory.SuggestionList:
                _emit_report_token(state, None)
                _emit_collab_retrieval_meta(state, used_l, used_t, 0)
            return {}
        out: dict = {
            "tool_steps": loop_res["tool_steps"],
            "collab_llm_calls": loop_res["collab_llm_calls"],
            "collab_tool_calls": loop_res["collab_tool_calls"],
        }
        extracted = None
        extract_ok = (budget.remaining(used_l + loop_res["collab_llm_calls"],
                                       settings.collab_max_llm_calls) > 0)
        if extract_ok and cost is not None and cost.exceeded is not None:
            extract_ok = False   # M42：预算已超 → 跳过 extract（不 raise，走优雅停）
        if extract_ok:
            extracted = await _extract(schema, prompt, loop_res["observations"],
                                       llm_config=llm_config)
            if extracted is not None:
                out["collab_llm_calls"] = loop_res["collab_llm_calls"] + 1
                if schema is memory.HypothesisList:
                    out["collab_hypotheses"] = [h.model_dump() for h in extracted.hypotheses]
                elif schema is memory.FindingList:
                    out["collab_findings"] = [f.model_dump() for f in extracted.findings]
                elif schema is memory.SuggestionList:
                    out["collab_suggestions"] = [s.model_dump() for s in extracted.suggestions]
        # I-2/I-4: refine 层优雅收尾——无论 extract 是否成功，都 emit 报告 token + retrieval meta
        if schema is memory.SuggestionList:
            _emit_report_token(state, extracted)
            total_l = used_l + int(out.get("collab_llm_calls", 0))
            total_t = used_t + int(out.get("collab_tool_calls", 0))
            _emit_collab_retrieval_meta(state, total_l, total_t,
                                        len(out.get("collab_suggestions", [])))
        return out


def _emit_report_token(state: dict, suggestions) -> None:
    """refine 层：把诊断报告作为 token 事件流出（用户在 SSE 流看到完整报告）。

    ``suggestions`` 为 ``SuggestionList`` 或 ``None``（extract 失败/预算耗尽）；
    None 时用已累积 WM + 空 suggestions 兜底汇总（I-2：保证至少一条 token，避免空响应）。
    """
    if (w := _safe_writer()) is None:
        return
    sug_list = ([s.model_dump() for s in suggestions.suggestions]
                if suggestions is not None else [])
    report = budget.build_collab_report(
        state.get("collab_hypotheses") or [],
        state.get("collab_findings") or [],
        sug_list,
    )
    try:
        w({"event": "token", "data": {"content": report}})
    except Exception:  # noqa: BLE001
        pass


def _emit_collab_retrieval_meta(state: dict, llm_calls: int, tool_calls: int,
                                suggestions_count: int) -> None:
    """refine 层收尾 emit 一条协作专用 retrieval meta（spec §8 / I-4）。

    ``mode="collab"`` + ``collab`` 指标段（hypotheses/findings/suggestions 计数 + LLM/工具
    调用数 + budget_exceeded），随 ``stream_graph`` 落 ``retrieval_logs`` JSONB，
    供 MonitorPage / 评测识别 collab 模式。
    """
    if (w := _safe_writer()) is None:
        return
    meta = {
        "mode": "collab",
        "tools": [t.name for t in (_DIAGNOSE_TOOLS + _VERIFY_TOOLS + _REFINE_TOOLS)],
        "terms": state.get("keywords", []),
        "recall": {"vector": 0, "lexical": 0, "graph": 0},
        "merged": 0,
        "coarse": None,
        "fine": 0,
        "rerank_on": False,
        "rewritten": state.get("rewritten", False),
        "embedding_strategy": settings.embedding_strategy,
        "collab": {
            "hypotheses": len(state.get("collab_hypotheses") or []),
            "findings": len(state.get("collab_findings") or []),
            "suggestions": suggestions_count,
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "budget_exceeded": (
                llm_calls >= settings.collab_max_llm_calls
                or tool_calls >= settings.collab_max_tool_calls
            ),
        },
    }
    try:
        w({"event": "retrieval", "data": meta})
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
