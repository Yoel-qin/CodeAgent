"""Task 10：WEB_SEARCH——配置解析纯函数 + 路由分支 + 节点降级 + 图装配。
不打真远程 MCP（load_tools 的网络路径由既有 TestClient 模式钉 noop）。"""
import json

from app.agent.query_analysis import RouteDecision, decide_route

# ── _parse_web_servers（纯函数） ───────────────────────────────────────────

def test_parse_web_servers_empty_and_bad(monkeypatch):
    from app.agent import tools_loader

    monkeypatch.setattr("app.core.config.settings.web_mcp_servers", "")
    assert tools_loader._parse_web_servers() == {}
    monkeypatch.setattr("app.core.config.settings.web_mcp_servers", "{not json")
    assert tools_loader._parse_web_servers() == {}
    monkeypatch.setattr("app.core.config.settings.web_mcp_servers", '["bad-shape"]')
    assert tools_loader._parse_web_servers() == {}  # 非 dict 元素跳过


def test_parse_web_servers_valid(monkeypatch):
    from app.agent import tools_loader

    raw = json.dumps([
        {"name": "tavily", "url": "http://example.com/sse", "transport": "sse"},
        {"name": "no-url"},  # 缺 url 跳过
        {"name": "plain", "url": "http://example.com/mcp"},  # transport 缺省 streamable_http
    ])
    monkeypatch.setattr("app.core.config.settings.web_mcp_servers", raw)
    assert tools_loader._parse_web_servers() == {
        "tavily": {"url": "http://example.com/sse", "transport": "sse"},
        "plain": {"url": "http://example.com/mcp", "transport": "streamable_http"},
    }


def test_default_transports_includes_web_only_when_configured(monkeypatch):
    from app.agent import tools_loader

    monkeypatch.setattr("app.core.config.settings.web_mcp_servers", "")
    assert "web" not in tools_loader._default_transports()
    monkeypatch.setattr("app.core.config.settings.web_mcp_servers",
                        json.dumps([{"name": "w", "url": "http://x/mcp"}]))
    assert tools_loader._default_transports()["web"] == {
        "w": {"url": "http://x/mcp", "transport": "streamable_http"}}


# ── 路由分支 ───────────────────────────────────────────────────────────────

def test_decide_route_web(monkeypatch):
    from app.agent import query_analysis

    monkeypatch.setattr(query_analysis, "get_web_tools", lambda: [])
    assert decide_route(RouteDecision(intent="web", confidence=0.9)) == "retrieve"
    monkeypatch.setattr(query_analysis, "get_web_tools", lambda: [object()])
    assert decide_route(RouteDecision(intent="web", confidence=0.9)) == "web_search"


# ── 节点：空工具降级 + 轮数旋钮 ────────────────────────────────────────────

async def test_web_search_node_degrades_without_tools(monkeypatch):
    from app.agent import react_base, web_search

    monkeypatch.setattr(web_search, "get_web_tools", lambda: [])
    monkeypatch.setattr(react_base, "configured", lambda: True)  # 有 key 也先撞空工具分支
    captured = {}

    async def _capture_retrieve(state, config):
        captured["called"] = True

    monkeypatch.setattr(react_base, "retrieve_node", _capture_retrieve)
    await web_search.web_search_node({"query": "q", "repo": "r", "history": []}, None)
    assert captured.get("called") is True


async def test_web_search_node_honors_rounds(monkeypatch):
    from app.agent import web_search

    captured = {}

    async def _fake_run(state, config, **kw):
        captured.update(kw)

    monkeypatch.setattr(web_search, "run_react_agent", _fake_run)
    monkeypatch.setattr(web_search, "get_web_tools", lambda: [object()])
    await web_search.web_search_node({"query": "q", "repo": "r", "history": []},
                                     {"configurable": {"rounds_web": 2}})
    assert captured["agent_name"] == "web_search" and captured["max_rounds"] == 2


# ── 图装配 ─────────────────────────────────────────────────────────────────

def test_graph_has_web_search_node():
    from app.agent.graph import GRAPH

    assert "web_search" in GRAPH.nodes
