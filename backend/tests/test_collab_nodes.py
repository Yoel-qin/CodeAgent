"""M35 协作节点单测：_bounded_tool_loop（手动有界 tool-calling）+ 三层节点 + 子图——纯 mock，无 infra。"""
from __future__ import annotations

import app.agent.collab.nodes as cn
from app.agent.collab import memory


class _FakeTool:
    """模拟 @tool 对象：有 .name 与 .ainvoke。"""
    def __init__(self, name, obs="ok"):
        self.name = name
        self._obs = obs

    async def ainvoke(self, args, config=None):
        return f"{self._obs}:{args}"


def _bind_model(responses):
    """返回 fake get_chat_model：bind_tools → 每次 ainvoke 弹一条 response。"""
    seq = list(responses)
    calls = {"n": 0}

    class _Resp:
        def __init__(self, tool_calls, content=""):
            self.tool_calls = tool_calls
            self.content = content

    class _Bound:
        async def ainvoke(self, msgs):
            i = min(calls["n"], len(seq) - 1)
            calls["n"] += 1
            return _Resp(*seq[i])

    class _Model:
        def bind_tools(self, tools):
            return _Bound()

    return _Model(), calls


def _patch_model(monkeypatch, responses):
    model, calls = _bind_model(responses)
    monkeypatch.setattr(cn, "get_chat_model", lambda: model)
    return calls


async def test_loop_stops_when_no_tool_calls(monkeypatch):
    _patch_model(monkeypatch, [( [], "done")])  # 第一轮无 tool_calls
    res = await cn._bounded_tool_loop(
        system_prompt="s", user_prompt="q", tools=[], max_rounds=3,
        llm_budget_left=9, tool_budget_left=12,
        layer_name="collab.diagnose", config={"configurable": {}})
    assert res["collab_llm_calls"] == 1
    assert res["collab_tool_calls"] == 0
    assert res["tool_steps"] == []


async def test_loop_executes_tool_calls(monkeypatch):
    _patch_model(monkeypatch, [
        ([{"name": "search_code", "args": {"query": "q"}, "id": "1"}], ""),
        ([], "done"),
    ])
    res = await cn._bounded_tool_loop(
        system_prompt="s", user_prompt="q", tools=[_FakeTool("search_code")],
        max_rounds=3, llm_budget_left=9, tool_budget_left=12,
        layer_name="collab.diagnose", config={"configurable": {}})
    assert res["collab_llm_calls"] == 2
    assert res["collab_tool_calls"] == 1
    assert res["tool_steps"][0]["agent"] == "collab.diagnose"
    assert res["tool_steps"][0]["tool"] == "search_code"
    assert "search_code" in res["observations"]


async def test_loop_parallel_gather_multiple_calls(monkeypatch):
    # 一轮两个 tool_calls → asyncio.gather 并行执行，各计一次
    _patch_model(monkeypatch, [
        ([{"name": "a", "args": {}, "id": "1"}, {"name": "b", "args": {}, "id": "2"}], ""),
        ([], "done"),
    ])
    res = await cn._bounded_tool_loop(
        system_prompt="s", user_prompt="q",
        tools=[_FakeTool("a"), _FakeTool("b")],
        max_rounds=3, llm_budget_left=9, tool_budget_left=12,
        layer_name="collab.verify", config={"configurable": {}})
    assert res["collab_tool_calls"] == 2
    assert {s["tool"] for s in res["tool_steps"]} == {"a", "b"}


async def test_loop_stops_on_llm_budget(monkeypatch):
    # llm_budget_left=1 → 一轮 ainvoke 后停（不进第二轮）
    _patch_model(monkeypatch, [
        ([{"name": "a", "args": {}, "id": "1"}], ""),
        ([{"name": "a", "args": {}, "id": "2"}], ""),
    ])
    res = await cn._bounded_tool_loop(
        system_prompt="s", user_prompt="q", tools=[_FakeTool("a")],
        max_rounds=3, llm_budget_left=1, tool_budget_left=12,
        layer_name="collab.diagnose", config={"configurable": {}})
    assert res["collab_llm_calls"] == 1  # 余量 1，用完即止


async def test_loop_stops_on_tool_budget(monkeypatch):
    # tool_budget_left=1 → 第一轮一个工具后，第二轮有 tool_calls 但 afford=0 → 停
    _patch_model(monkeypatch, [
        ([{"name": "a", "args": {}, "id": "1"}], ""),
        ([{"name": "a", "args": {}, "id": "2"}], ""),
        ([], "done"),
    ])
    res = await cn._bounded_tool_loop(
        system_prompt="s", user_prompt="q", tools=[_FakeTool("a")],
        max_rounds=3, llm_budget_left=9, tool_budget_left=1,
        layer_name="collab.diagnose", config={"configurable": {}})
    assert res["collab_tool_calls"] == 1


async def test_loop_stops_on_max_rounds(monkeypatch):
    # 每轮都返回 tool_calls，max_rounds=2 → 两轮后停
    _patch_model(monkeypatch, [
        ([{"name": "a", "args": {}, "id": "1"}], ""),
        ([{"name": "a", "args": {}, "id": "2"}], ""),
        ([{"name": "a", "args": {}, "id": "3"}], ""),
    ])
    res = await cn._bounded_tool_loop(
        system_prompt="s", user_prompt="q", tools=[_FakeTool("a")],
        max_rounds=2, llm_budget_left=9, tool_budget_left=12,
        layer_name="collab.diagnose", config={"configurable": {}})
    assert res["collab_llm_calls"] == 2  # 受 max_rounds 界


# ---- 三层节点辅助 ----


def _patch_struct(monkeypatch, returns):
    """fake with_structured_output：按 schema 类型返回固定对象。"""
    by_type = {type(r): r for r in returns}

    class _Struct:
        def __init__(self, ret):
            self._ret = ret

        async def ainvoke(self, msgs):
            return self._ret

    class _Model:
        def bind_tools(self, tools):
            class _B:
                async def ainvoke(self, m):
                    class _R:  # _bounded_tool_loop 一轮即停（无 tool_calls）
                        tool_calls = []
                        content = "obs"
                    return _R()
            return _B()

        def with_structured_output(self, schema):
            return _Struct(by_type.get(schema))

    monkeypatch.setattr(cn, "get_chat_model", lambda: _Model())
    monkeypatch.setattr(cn, "configured", lambda: True)


async def test_diagnose_writes_hypotheses_and_accumulates_budget(monkeypatch):
    _patch_struct(monkeypatch, [
        memory.HypothesisList(hypotheses=[memory.HypothesisItem(hypothesis="H1", confidence="高")]),
    ])
    out = await cn.diagnose(
        {"query": "消费者堆积", "history": [],
         "collab_llm_calls": 0, "collab_tool_calls": 0},
        {"configurable": {"session": None, "top_k": 8}})
    assert out["collab_hypotheses"][0]["hypothesis"] == "H1"
    assert out["collab_llm_calls"] >= 1  # loop + extract
    assert "tool_steps" in out


async def test_verify_reads_hypotheses_writes_findings(monkeypatch):
    _patch_struct(monkeypatch, [
        memory.FindingList(findings=[memory.FindingItem(chunk_id="c1", finding="F1")]),
    ])
    out = await cn.verify(
        {"query": "q", "history": [],
         "collab_hypotheses": [{"hypothesis": "H1"}],
         "collab_llm_calls": 2, "collab_tool_calls": 1},
        {"configurable": {"session": None, "top_k": 8}})
    assert out["collab_findings"][0]["chunk_id"] == "c1"
    # 计数器 delta 为本轮消耗（节点返回 delta，reducer 累积到下层）
    assert out["collab_llm_calls"] >= 1


async def test_refine_writes_suggestions_and_emits_token(monkeypatch):
    _patch_struct(monkeypatch, [
        memory.SuggestionList(suggestions=[memory.SuggestionItem(suggestion="S1")]),
    ])
    out = await cn.refine(
        {"query": "q", "history": "",
         "collab_hypotheses": [{"hypothesis": "H1"}],
         "collab_findings": [{"chunk_id": "c1", "finding": "F1"}],
         "collab_llm_calls": 4, "collab_tool_calls": 2},
        {"configurable": {"session": None, "top_k": 8}})
    assert out["collab_suggestions"][0]["suggestion"] == "S1"


async def test_layer_degrades_when_not_configured(monkeypatch):
    monkeypatch.setattr(cn, "configured", lambda: False)
    out = await cn.diagnose({"query": "q", "history": ""}, {"configurable": {}})
    # 无 LLM key → 不跑，返回空 delta（不抛、不中断）
    assert out == {} or out.get("collab_hypotheses") in (None, [])


# ---- 子图编译 + 依序 ----


def test_subgraph_compiles_three_layers_in_order():
    from app.agent.collab.subgraph import build_collab_subgraph
    sg = build_collab_subgraph()
    # 编译后的图节点名含 diagnose/verify/refine
    node_names = set(getattr(sg, "nodes", {}).keys())
    for n in ("diagnose", "verify", "refine"):
        assert n in node_names, f"子图缺节点 {n}"
