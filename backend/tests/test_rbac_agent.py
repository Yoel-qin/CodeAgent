"""M45 agent 层 RBAC 测试（零 infra）：路由门控 + 工具防御 + retrieve/_degrade 透传。"""
from __future__ import annotations

_EXT = {"doc", "table", "image"}   # external 白名单


# ---- 路由门控：无 code 权限 → code 侧 agent 一律 retrieve ----


def test_route_gates_code_side_agents(monkeypatch):
    import app.agent.nodes.router as rt

    monkeypatch.setattr(rt, "configured", lambda: True)
    monkeypatch.setattr(rt.settings, "multi_agent_collab_enabled", False)

    cfg = {"configurable": {"allowed_kinds": _EXT}}
    for intent in ("code", "bug", "review", "test", "graph"):
        assert rt.route({"intent": intent, "agent_type": None}, cfg) == "retrieve"
    for dom in ("trace", "diagnose", "tune"):
        state = {"intent": dom, "agent_type": None, "active_pack_name": "rocketmq"}
        assert rt.route(state, cfg) == "retrieve"

    # doc 意图不受影响（doc 工具自身过滤）
    assert rt.route({"intent": "doc", "agent_type": None}, cfg) == "doc_answer"
    # 不限制（off）→ 现状
    cfg_full = {"configurable": {"allowed_kinds": None}}
    assert rt.route({"intent": "code", "agent_type": None}, cfg_full) == "code_understand"


def test_route_gates_collab_and_doc_maintain(monkeypatch):
    import app.agent.nodes.router as rt

    monkeypatch.setattr(rt, "configured", lambda: True)
    monkeypatch.setattr(rt.settings, "multi_agent_collab_enabled", True)

    cfg = {"configurable": {"allowed_kinds": _EXT}}
    assert rt.route({"intent": "mixed", "needs_collab": True}, cfg) == "retrieve"
    cfg_full = {"configurable": {"allowed_kinds": None}}
    assert rt.route({"intent": "mixed", "needs_collab": True}, cfg_full) == "collab"

    # DOC_MAINTAIN 显式 agent_type：无 writeops 权限（external）→ retrieve
    assert rt.route({"intent": None, "agent_type": "DOC_MAINTAIN"}, cfg) == "retrieve"
    assert rt.route({"intent": None, "agent_type": "DOC_MAINTAIN"}, cfg_full) == "propose"


# ---- 工具防御：read_code 无权 → 文本提示不崩 ----


async def test_read_code_denied_returns_notice():
    from app.agent.tools.code_tools import read_code as _rc

    res = await _rc.ainvoke(
        {"chunk_id": "code_x"},
        config={"configurable": {"session": None, "allowed_kinds": _EXT}},
    )
    assert "无权" in res


async def test_read_code_allowed_passes(monkeypatch):
    import app.agent.tools.code_tools as ct
    from app.agent.tools.code_tools import read_code as _rc

    async def fake_logic(cid, session):
        return ct.ToolResult("ok detail", [])

    monkeypatch.setattr(ct, "_read_code", fake_logic)
    res = await _rc.ainvoke(
        {"chunk_id": "code_x"},
        config={"configurable": {"session": None, "allowed_kinds": None}},
    )
    assert res == "ok detail"


# ---- retrieve 节点透传（走桩 pipeline）----


async def test_retrieve_node_threads_allowed_kinds(monkeypatch):
    import app.agent.nodes.retrieve as rn

    captured: dict = {}

    class _Pipe:
        async def recall(self, session, query, **kw):
            captured.update(kw)
            return [], {"recall": {}, "merged": 0}

    monkeypatch.setattr(rn, "pipeline", _Pipe())
    async def _no_enrich(s, r):
        pass
    monkeypatch.setattr(rn, "_enrich_content_types", _no_enrich)
    monkeypatch.setattr(rn, "get_stream_writer", lambda: lambda d: None)
    monkeypatch.setattr("app.clients.cache_client.get_cache_client", lambda: None)

    state = {"query": "q", "semantic_query": "q", "keywords": [], "rewritten": False}
    cfg = {"configurable": {"session": None, "top_k": 5, "allowed_kinds": _EXT,
                            "qa_cache": None}}
    await rn.retrieve(state, cfg)
    assert captured.get("allowed_kinds") == _EXT


# ---- 工具防御：get_related_code 无 code 权限 → 文本提示不崩 ----


async def test_get_related_code_denied_returns_notice():
    from app.agent.tools.doc_tools import get_related_code as _grc

    res = await _grc.ainvoke(
        {"center": "doc_x"},
        config={"configurable": {"session": None, "allowed_kinds": _EXT}},
    )
    assert "无权" in res
