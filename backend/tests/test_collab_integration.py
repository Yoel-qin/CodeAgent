"""M35 集成测：collab wrapper 事件桥接（I-5）+ 整体降级伞（I-1）+ retrieval meta（I-4）。

final review I-5：collab 子图经 ``add_node`` 嵌入主图，其 ``agent_step``/``citation``/``token``
custom 事件能否到达父图 ``astream(stream_mode="custom")`` 流——本测用真实 ``collab_node`` wrapper
（手动 astream 子图 + parent_writer 转发）验证桥接有效，并断言 retrieval meta 含 mode="collab"。
"""
from __future__ import annotations

import app.agent.collab.nodes as cn
from app.agent.collab import memory
from app.agent.graph import collab_node


async def test_collab_wrapper_bridges_custom_events(monkeypatch):
    """I-5: wrapper astream 子图 → 转发 agent_step/token/retrieval 事件到父流。"""
    # mock 三层 LLM 调用：_bounded_tool_loop emit 一条 agent_step + 返回最小 delta；
    # _extract 各 schema 返回单条产出，确保 refine 走成功路径 + 兜底 emit retrieval meta。
    async def fake_loop(*, system_prompt, user_prompt, tools, max_rounds, llm_budget_left,
                        tool_budget_left, layer_name, config):
        cn._emit_agent_step(layer_name, "search_code", {"query": "q"})
        return {"tool_steps": [{"agent": layer_name, "tool": "search_code", "args": {"query": "q"}}],
                "observations": "obs", "collab_llm_calls": 1, "collab_tool_calls": 1}
    monkeypatch.setattr(cn, "_bounded_tool_loop", fake_loop)
    monkeypatch.setattr(cn, "configured", lambda: True)

    async def fake_extract(schema, prompt, observations):
        if schema is memory.HypothesisList:
            return memory.HypothesisList(hypotheses=[memory.HypothesisItem(hypothesis="H1")])
        if schema is memory.FindingList:
            return memory.FindingList(findings=[memory.FindingItem(chunk_id="c1", finding="F1")])
        if schema is memory.SuggestionList:
            return memory.SuggestionList(suggestions=[memory.SuggestionItem(suggestion="S1")])
        return None
    monkeypatch.setattr(cn, "_extract", fake_extract)

    # 父流收集器：替换 graph._safe_writer 让 wrapper 的 parent_writer 收集转发事件
    events: list[dict] = []
    monkeypatch.setattr("app.agent.graph._safe_writer", lambda: events.append)

    state = {
        "query": "消费者堆积", "history": [],
        "collab_llm_calls": 0, "collab_tool_calls": 0,
        "collab_hypotheses": [], "collab_findings": [], "collab_suggestions": [],
    }
    config = {"configurable": {"session": None, "top_k": 8}}
    await collab_node(state, config)

    ev_types = {e.get("event") for e in events}
    # 桥接的 agent_step（三层各一条）+ token（refine 报告）+ retrieval（mode=collab 指标）
    assert "agent_step" in ev_types, f"agent_step 未桥接到父流：{events}"
    assert "token" in ev_types, f"token 未桥接到父流：{events}"
    assert "retrieval" in ev_types, f"retrieval 未桥接到父流：{events}"
    # agent_step 带层标签
    agent_steps = [e["data"] for e in events if e.get("event") == "agent_step"]
    layer_agents = {d.get("agent") for d in agent_steps}
    assert any(a and a.startswith("collab.") for a in layer_agents), layer_agents
    # I-4: retrieval meta 含 mode=collab + 协作指标
    retrieval = next(e["data"] for e in events if e.get("event") == "retrieval")
    assert retrieval["mode"] == "collab"
    assert retrieval["collab"]["hypotheses"] == 1
    assert retrieval["collab"]["findings"] == 1
    assert retrieval["collab"]["suggestions"] == 1
    assert retrieval["collab"]["llm_calls"] >= 3
    assert retrieval["collab"]["tool_calls"] >= 3


async def test_collab_wrapper_degrades_on_subgraph_exception(monkeypatch):
    """I-1: 子图抛未兜住异常 → wrapper catch → _degrade（请求不中断、不冒泡）。"""
    from langgraph.graph import END, START, StateGraph

    from app.agent.state import AgentState

    async def boom_node(state, config):
        raise RuntimeError("subgraph boom")

    sg = StateGraph(AgentState)
    sg.add_node("diagnose", boom_node)
    sg.add_edge(START, "diagnose")
    sg.add_edge("diagnose", END)
    compiled = sg.compile()
    monkeypatch.setattr("app.agent.graph._collab_subgraph", compiled)

    called = {"degrade": False, "err": None}

    async def fake_degrade(state, config, err, *, degrade_label):
        called["degrade"] = True
        called["err"] = err
        assert degrade_label == "多 Agent 协作"
    monkeypatch.setattr("app.agent.graph._degrade", fake_degrade)

    # 不抛即说明 wrapper 兜住异常
    await collab_node({"query": "q", "history": []},
                      {"configurable": {"session": None, "top_k": 8}})
    assert called["degrade"] is True
    assert isinstance(called["err"], RuntimeError)


async def test_collab_refine_emits_report_token_on_extract_failure(monkeypatch):
    """I-2: refine 层 extract 失败/预算耗尽时，仍 emit 报告 token（兜底防空响应）。

    构造 LLM 预算已耗尽（collab_llm_calls 达上限）→ _run_layer 不调 extract，
    但 refine 末尾兜底仍 emit token + retrieval meta（用空 suggestions + 已累积 WM）。
    """
    from app.agent.collab.budget import build_collab_report

    async def fake_loop(*, system_prompt, user_prompt, tools, max_rounds, llm_budget_left,
                        tool_budget_left, layer_name, config):
        return {"tool_steps": [], "observations": "", "collab_llm_calls": 0, "collab_tool_calls": 0}
    monkeypatch.setattr(cn, "_bounded_tool_loop", fake_loop)
    monkeypatch.setattr(cn, "configured", lambda: True)

    events: list[dict] = []
    monkeypatch.setattr(cn, "_safe_writer", lambda: events.append)
    # 关键：collab_max_llm_calls 与 used_l 相等 → remaining=0 → 跳过 extract
    monkeypatch.setattr(cn.settings, "collab_max_llm_calls", 5)

    state = {
        "query": "q", "history": [],
        "collab_llm_calls": 5, "collab_tool_calls": 0,  # LLM 预算已耗尽
        "collab_hypotheses": [{"hypothesis": "H1", "confidence": "中"}],
        "collab_findings": [{"chunk_id": "c1", "finding": "F1", "verdict": "supports"}],
        "collab_suggestions": [],
    }
    out = await cn.refine(state, {"configurable": {"session": None, "top_k": 8}})
    # extract 未跑（预算耗尽）→ 无 collab_suggestions delta
    assert "collab_suggestions" not in out or not out["collab_suggestions"]
    # 兜底仍 emit token（报告）+ retrieval meta
    ev_types = {e.get("event") for e in events}
    assert "token" in ev_types
    assert "retrieval" in ev_types
    token_ev = next(e["data"] for e in events if e.get("event") == "token")
    # 报告含已累积 WM（H1/F1），不含 suggestions
    assert "H1" in token_ev["content"]
    assert "F1" in token_ev["content"]
    # 验证用的是 build_collab_report 兜底文案路径（非空内容）
    assert token_ev["content"]  # 非空
    # retrieval meta 标 budget_exceeded
    retrieval = next(e["data"] for e in events if e.get("event") == "retrieval")
    assert retrieval["mode"] == "collab"
    assert retrieval["collab"]["budget_exceeded"] is True
    assert retrieval["collab"]["suggestions"] == 0
    # 顺便覆盖 build_collab_report 空路径（同 M-1 deferred 兜底文案）
    assert "未产出" in build_collab_report([], [], []) or "未能在检索结果中" in build_collab_report([], [], [])


async def test_bounded_tool_loop_handles_unknown_tool_name(monkeypatch):
    """I-3: LLM 幻觉工具名（schema 外）→ 跳过该工具、记 step、不 KeyError。"""
    # fake model 返回幻觉工具名
    class _Resp:
        def __init__(self, tool_calls):
            self.tool_calls = tool_calls
            self.content = ""

    class _Bound:
        def __init__(self, seq):
            self._seq = list(seq)
            self._n = 0

        async def ainvoke(self, msgs, **kw):
            i = min(self._n, len(self._seq) - 1)
            self._n += 1
            return _Resp(self._seq[i])

    class _Model:
        def bind_tools(self, tools):
            return _Bound([[{"name": "hallucinated_tool", "args": {}, "id": "1"}], []])

    monkeypatch.setattr(cn, "get_chat_model", lambda: _Model())

    events: list[dict] = []
    monkeypatch.setattr(cn, "_safe_writer", lambda: events.append)

    res = await cn._bounded_tool_loop(
        system_prompt="s", user_prompt="q",
        tools=[cn.search_code],  # 真工具集里没有 hallucinated_tool
        max_rounds=2, llm_budget_left=9, tool_budget_left=12,
        layer_name="collab.diagnose", config={"configurable": {}})
    # 幻觉工具被跳过 → tool_calls=0
    assert res["collab_tool_calls"] == 0
    assert res["tool_steps"] == []
    # 记了一条 unknown_tool step
    unknown_steps = [e["data"] for e in events
                     if e.get("event") == "agent_step" and e["data"].get("tool") == "(unknown_tool)"]
    assert len(unknown_steps) == 1
    assert unknown_steps[0]["args"]["name"] == "hallucinated_tool"


async def test_bounded_tool_loop_isolates_single_tool_exception(monkeypatch):
    """I-3: 一轮中单个工具 ainvoke 抛异常 → 记 error step、其他工具仍执行、不炸层。"""
    class _Resp:
        def __init__(self, tool_calls):
            self.tool_calls = tool_calls
            self.content = ""

    class _Bound:
        def __init__(self, seq):
            self._seq = list(seq)
            self._n = 0

        async def ainvoke(self, msgs, **kw):
            i = min(self._n, len(self._seq) - 1)
            self._n += 1
            return _Resp(self._seq[i])

    class _Model:
        def bind_tools(self, tools):
            return _Bound([
                [{"name": "boom", "args": {}, "id": "1"},
                 {"name": "ok", "args": {}, "id": "2"}],
                [],
            ])

    class _BoomTool:
        name = "boom"

        async def ainvoke(self, args, config=None):
            raise RuntimeError("tool boom")

    class _OkTool:
        name = "ok"

        async def ainvoke(self, args, config=None):
            return "ok-result"

    monkeypatch.setattr(cn, "get_chat_model", lambda: _Model())
    events: list[dict] = []
    monkeypatch.setattr(cn, "_safe_writer", lambda: events.append)

    res = await cn._bounded_tool_loop(
        system_prompt="s", user_prompt="q",
        tools=[_BoomTool(), _OkTool()],
        max_rounds=2, llm_budget_left=9, tool_budget_left=12,
        layer_name="collab.verify", config={"configurable": {}})
    # 一个工具 boom 抛异常被隔离 → 另一个 ok 仍执行
    assert res["collab_tool_calls"] == 1
    tool_names = {s["tool"] for s in res["tool_steps"]}
    assert tool_names == {"ok"}
    # 记了一条 error step
    error_steps = [e["data"] for e in events
                   if e.get("event") == "agent_step" and e["data"].get("tool") == "(error)"]
    assert len(error_steps) == 1
    assert "RuntimeError" in error_steps[0]["args"]["error"]
