"""M42 预算拦截测试：chunk 循环 / collab 每轮 / _degrade 模板降级。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.agent.agents._base as base
from app.agent.cost import BudgetExceeded, CostController


def _exceeded_ctl(reason="tokens"):
    c = CostController(max_tokens=5, max_llm_calls=9)
    c.record_usage(prompt=99) if reason == "tokens" else None
    if reason == "llm_calls":
        c2 = CostController(max_tokens=999, max_llm_calls=1)
        c2.record_call()
        c2.record_call()
        return c2
    return c


def _writer_spy(events):
    def w(chunk):
        events.append(chunk)
    return w


@pytest.mark.asyncio
async def test_degrade_budget_template(monkeypatch):
    """BudgetExceeded → _degrade 不烧 LLM，emit 模板 notice + citations。"""
    events: list = []
    monkeypatch.setattr(base, "_safe_writer", lambda: _writer_spy(events))
    ranked = [{"chunk_id": "c1", "kind": "code", "label": "L", "content": "x",
               "path": "p", "score": 1.0, "content_type": "text"}]
    monkeypatch.setattr(base.pipeline, "recall",
                        AsyncMock(return_value=(ranked, {"merged": 1})))
    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(base, "_enrich_content_types", _noop)

    async def _must_not_stream(*a, **kw):
        raise AssertionError("BudgetExceeded 降级不得再调 LLM stream_tokens")
    monkeypatch.setattr(base, "llm", SimpleNamespace(configured=True, stream_tokens=_must_not_stream))

    err = _exceeded_ctl().exceeded
    await base._degrade({"query": "q"}, {"configurable": {
        "session": None, "top_k": 8, "agent_type": None}}, err, degrade_label="测试")
    tokens = [e["data"]["content"] for e in events if e["event"] == "token"]
    assert any("预算" in t for t in tokens)            # 模板 notice
    assert any(e["event"] == "citation" for e in events)  # citations 照发


@pytest.mark.asyncio
async def test_run_scenario_agent_raises_on_exceeded(monkeypatch):
    """chunk 循环轮询 cost.exceeded → raise → except → _degrade(err=BudgetExceeded)。"""
    degraded: list = []

    async def _fake_degrade(state, config, err, *, degrade_label):
        degraded.append(err)
        return None
    monkeypatch.setattr(base, "_degrade", _fake_degrade)
    monkeypatch.setattr(base, "configured", lambda: True)

    class _FakeAgent:
        async def astream(self, *a, **kw):
            for i in range(100):                       # 无限产 chunk，靠拦截断
                yield {"event": "agent_step", "data": {"n": i}}

    monkeypatch.setattr(base, "_safe_writer", lambda: None)
    cost = _exceeded_ctl()
    await base.run_scenario_agent(
        {"query": "q"}, {"configurable": {"session": None, "top_k": 8, "agent_type": None,
                                          "cost": cost}},
        agent_name="fake", tools=[], build_agent=lambda: _FakeAgent(),
        degrade_label="测试")
    assert len(degraded) == 1
    assert isinstance(degraded[0], BudgetExceeded)


@pytest.mark.asyncio
async def test_collab_loop_stops_on_exceeded(monkeypatch):
    """collab _bounded_tool_loop：已超限 → 循环顶 check() 抛 BudgetExceeded，零 ainvoke。"""
    import app.agent.collab.nodes as cn
    ainvoke = AsyncMock()

    class _FakeBind:
        async def ainvoke(self, messages, config=None):
            ainvoke()
            class _R:
                tool_calls = []
            return _R()

    monkeypatch.setattr(cn, "get_chat_model", lambda: SimpleNamespace(bind_tools=lambda t: _FakeBind()))
    cost = _exceeded_ctl()
    with pytest.raises(BudgetExceeded):
        await cn._bounded_tool_loop(
            system_prompt="s", user_prompt="u", tools=[], max_rounds=3,
            llm_budget_left=9, tool_budget_left=9, layer_name="collab.diagnose",
            config={"configurable": {"cost": cost}}, llm_config=None)
    assert ainvoke.call_count == 0   # raise 先于任何模型调用
