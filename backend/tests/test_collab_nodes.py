"""M35 协作节点单测：_bounded_tool_loop（手动有界 tool-calling）——纯 mock，无 infra。"""
from __future__ import annotations

import app.agent.collab.nodes as cn


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
