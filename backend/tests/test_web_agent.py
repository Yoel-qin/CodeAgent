"""联网 Agent 工具/节点单测（agent/tools/web_tools.py + agent/agents/web.py）——纯/mock。

覆盖：``get_web_tools`` 未启用为空、``init_web_tools`` 无客户端/有客户端两条路、
``_wrap_for_step`` 调用后发 ``agent_step``、``get_web_agent`` 无工具时返 None。
不触网、不依赖 langchain-mcp-adapters（fake client）。
"""
from __future__ import annotations

import pytest

import app.agent.tools.web_tools as wt

# ---- 一个远程工具的替身（真实 StructuredTool，让 _wrap_for_step 的 schema 透传可验证）----


def _make_remote_tool():
    from langchain_core.tools import tool

    @tool
    async def remote_search(query: str) -> str:
        """search the web"""
        return "result:" + query

    return remote_search


@pytest.fixture
def reset_web_tools():
    wt._web_tools = []
    yield
    wt._web_tools = []


# ---- get_web_tools / init_web_tools ----


def test_get_web_tools_empty_before_init(reset_web_tools):
    assert wt.get_web_tools() == []


async def test_init_web_tools_empty_when_no_client(monkeypatch, reset_web_tools):
    monkeypatch.setattr(wt, "get_mcp_client", lambda: None)
    await wt.init_web_tools()
    assert wt.get_web_tools() == []


async def test_init_web_tools_loads_and_wraps(monkeypatch, reset_web_tools):
    remote = _make_remote_tool()

    class FakeClient:
        async def get_tools(self):
            return [remote]

    monkeypatch.setattr(wt, "get_mcp_client", lambda: FakeClient())
    await wt.init_web_tools()

    tools = wt.get_web_tools()
    assert len(tools) == 1
    assert tools[0].name == "remote_search"  # 远程工具名透传


async def test_init_web_tools_failure_clears(monkeypatch, reset_web_tools):
    """get_tools() 抛错 → _web_tools 置空（降级），不抛。"""
    monkeypatch.setattr(wt, "_web_tools", [object()])  # 假设此前有残留

    class BoomClient:
        async def get_tools(self):
            raise RuntimeError("server down")

    monkeypatch.setattr(wt, "get_mcp_client", lambda: BoomClient())
    await wt.init_web_tools()
    assert wt.get_web_tools() == []


# ---- _wrap_for_step：调用后发 agent_step ----


async def test_wrap_for_step_emits_agent_step(monkeypatch):
    captured: list[dict] = []

    def fake_get_writer():
        def _w(evt: dict) -> None:
            captured.append(evt)
        return _w

    monkeypatch.setattr(wt, "get_stream_writer", fake_get_writer)

    wrapped = wt._wrap_for_step(_make_remote_tool())
    out = await wrapped.ainvoke({"query": "hello"})
    assert out == "result:hello"
    assert any(
        e["event"] == "agent_step" and e["data"]["tool"] == "remote_search"
        and e["data"]["n"] == 1
        for e in captured
    )


async def test_wrap_for_step_failure_emits_step_and_returns_notice(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(wt, "get_stream_writer", lambda: (lambda e: captured.append(e)))

    def _make_bad():
        from langchain_core.tools import tool

        @tool
        async def bad(query: str) -> str:
            """bad"""
            raise RuntimeError("boom")

        return bad

    wrapped = wt._wrap_for_step(_make_bad())
    out = await wrapped.ainvoke({"query": "x"})
    assert "调用失败" in out  # 降级为文本提示，单工具失败不杀 Agent
    assert any(e["data"]["n"] == 0 for e in captured if e["event"] == "agent_step")


# ---- get_web_agent：无工具时返 None（不建空 Agent）----


def test_get_web_agent_none_when_no_tools(monkeypatch):
    import app.agent.agents.web as web_mod

    monkeypatch.setattr(web_mod, "_agent", None)
    monkeypatch.setattr(web_mod, "get_web_tools", lambda: [])
    assert web_mod.get_web_agent() is None
