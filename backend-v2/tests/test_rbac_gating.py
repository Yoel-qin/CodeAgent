"""Task 9：作用域门控穿透。off 态（scopes=None）零行为变更 + on 态三路门。
API 层用 dependency_overrides 注入伪用户（不打真 JWT——认证逻辑 Task 8 已测）。

与 brief 逐字文本的唯一适配（沿 test_auth_rbac.rbac_on 先例）：三个 on 态 API 测试
补 ``monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret")``——
``app.main`` lifespan 在 ``rbac_enabled`` 且 ``jwt_secret`` 空时启动即抛
RuntimeError（fail-fast），而 jwt_secret 默认空（本机 .env 与 CI 均无值），
不补则 TestClient 进不了 lifespan，测试根本到不了被测的门。
"""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from app.agent import tools_loader
    from app.db.base import engine

    async def _noop_load(transports=None):
        return None

    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    yield
    slog = logging.getLogger("sqlalchemy")
    prev, slog.level = slog.level, logging.CRITICAL + 1
    try:
        import asyncio

        asyncio.run(engine.dispose())
    finally:
        slog.setLevel(prev)


# ── 纯函数：repo_visible / request_scopes ──────────────────────────────────

def test_repo_visible_and_request_scopes(monkeypatch):
    from app.api.deps import normalize_scopes, repo_visible, request_scopes

    monkeypatch.setattr(settings, "rbac_enabled", False)
    assert repo_visible({}, "any") is True and request_scopes({}) is None

    monkeypatch.setattr(settings, "rbac_enabled", True)
    user = {"allowed_scopes": {"repos": ["rocketmq"], "kinds": ["doc"]}}
    assert repo_visible(user, "rocketmq") and not repo_visible(user, "other")
    scopes = request_scopes(user)
    assert scopes == {"repos": {"rocketmq"}, "kinds": {"doc"}}
    assert normalize_scopes({"repos": ["*"], "kinds": []})["kinds"] == {"code", "doc"}


# ── 图内门 1：query_analysis 无权 intent 改路由 ────────────────────────────

async def test_query_analysis_gates_code_intent(monkeypatch):
    from app.agent import query_analysis

    monkeypatch.setattr(query_analysis, "configured", lambda: False)
    state = {"query": "CommitLog putMessage 在哪", "repo": "r", "history": []}
    # 无门控：规则路判 code → codenav
    out = await query_analysis.query_analysis_node(state, None)
    assert out["route"] == "codenav"
    # 有门控（只 doc 域）：intent 仍 code（诚实记录），路由改 retrieve
    cfg = {"configurable": {"scopes": {"repos": "*", "kinds": {"doc"}}}}
    out2 = await query_analysis.query_analysis_node(state, cfg)
    assert out2["intent"] == "code" and out2["route"] == "retrieve"


# ── 图内门 2：retrieve_node 按域跳路 ────────────────────────────────────────

async def test_retrieve_node_skips_forbidden_kind(monkeypatch):
    from app.agent import nodes

    monkeypatch.setattr(nodes, "configured", lambda: False)
    calls = {"hybrid": 0, "grep": 0}
    monkeypatch.setattr(nodes, "hybrid_search",
                        lambda *a: calls.__setitem__("hybrid", calls["hybrid"] + 1) or {"results": []})
    monkeypatch.setattr(nodes, "grep_code",
                        lambda *a: calls.__setitem__("grep", calls["grep"] + 1)
                        or {"matches": [], "total_count": 0, "truncated": False, "engine": "python"})
    state = {"query": "CommitLog putMessage 查询", "repo": "mini", "history": []}
    # 只 code 域：doc 路（hybrid）被跳过
    await nodes.retrieve_node(state, {"configurable": {
        "scopes": {"repos": "*", "kinds": {"code"}}}})
    assert calls == {"hybrid": 0, "grep": 2}  # query 提取 2 个英文标识符各跑一次 grep
    # 只 doc 域：code 路（grep）被跳过
    calls["grep"] = 0
    await nodes.retrieve_node(state, {"configurable": {
        "scopes": {"repos": "*", "kinds": {"doc"}}}})
    assert calls == {"hybrid": 1, "grep": 0}


# ── 图内门 3：wrap_tool 域防御 ─────────────────────────────────────────────

async def test_wrap_tool_blocks_forbidden_domain():
    from langchain_core.tools import StructuredTool

    from app.agent.tools_loader import TOOL_DOMAIN, ToolCallTracker, wrap_tool

    TOOL_DOMAIN["grep_code"] = "code"  # 测试直写（生产由 load_tools 填充）
    try:
        async def _inner(**_kw):
            return json.dumps({"matches": []})

        # args_schema 显式给（沿 test_chat_api._doc_tool 先例）：本仓 langchain-core 对
        # **kwargs-only coroutine 推不出 schema，args_schema 缺失时 StructuredTool 构造期即 422
        tool = StructuredTool(
            name="grep_code", description="t",
            args_schema={"type": "object", "properties": {}, "additionalProperties": True},
            coroutine=_inner)
        tracker = ToolCallTracker()
        wrapped = wrap_tool(tool, tracker,
                            scopes={"repos": "*", "kinds": {"doc"}})
        out = await wrapped.ainvoke({})
        assert "no permission" in out  # 被拦：返回 error JSON 而非执行
        # M9 加固轮：被拦截调用记 blocked 步（trace 可观测，不再是无痕拒绝）
        assert tracker.steps and tracker.steps[-1]["blocked"] is True \
            and tracker.steps[-1]["tool"] == "grep_code"
        ok = wrap_tool(tool, ToolCallTracker(), scopes={"repos": "*", "kinds": {"code", "doc"}})
        assert "no permission" not in await ok.ainvoke({})
        no_gate = wrap_tool(tool, ToolCallTracker())  # scopes=None（off 态）零行为变更
        assert "no permission" not in await no_gate.ainvoke({})
    finally:
        TOOL_DOMAIN.pop("grep_code", None)


# ── Fix R1（评审 Important 1）：wrap_tool repo 维度门 ───────────────────────

async def test_wrap_tool_gates_repo_visibility():
    """工具层 repo 门：LLM 显式传不可见 repo 拦截（error JSON 不执行）；可见透传 /
    缺省注入 / "*" 全放 / off 态照旧。工具实参由 LLM 产出，HTTP 层 repo 门拦不到
    agent 工具调用——此门补上图内最后一环。"""
    from langchain_core.tools import StructuredTool

    from app.agent.tools_loader import ToolCallTracker, wrap_tool

    async def _inner(repo="", **_kw):
        return json.dumps({"echo_repo": repo, "ok": True})

    tool = StructuredTool(
        name="grep_code", description="t",
        args_schema={"type": "object",
                     "properties": {"repo": {"type": "string", "default": ""}}},
        coroutine=_inner)
    scopes = {"repos": {"a"}, "kinds": {"code", "doc"}}

    # a) LLM 显式传不可见 repo → 拦截（error JSON，不执行）
    blocked = wrap_tool(tool, ToolCallTracker(), scopes=scopes)
    out = await blocked.ainvoke({"repo": "b"})
    assert "no permission" in out and "echo_repo" not in out
    # b) 显式传可见 repo → 放行透传（LLM 显式值不被覆盖）
    out2 = await wrap_tool(tool, ToolCallTracker(), scopes=scopes).ainvoke({"repo": "a"})
    assert "no permission" not in out2 and '"echo_repo": "a"' in out2
    # c) 未传 repo、default_repo 可见 → 放行，缺省注入照旧
    out3 = await wrap_tool(tool, ToolCallTracker(), default_repo="a",
                           scopes=scopes).ainvoke({})
    assert "no permission" not in out3 and '"echo_repo": "a"' in out3
    # 会话 repo 不可见且 LLM 未显式传 → fail-closed（缺省/空 repo 不成越权通道）
    fc = wrap_tool(tool, ToolCallTracker(), default_repo="b", scopes=scopes)
    assert "no permission" in await fc.ainvoke({})
    # repos="*" 全放（任意 repo 透传）
    star = {"repos": "*", "kinds": {"code", "doc"}}
    out4 = await wrap_tool(tool, ToolCallTracker(), scopes=star).ainvoke({"repo": "any"})
    assert "no permission" not in out4 and '"echo_repo": "any"' in out4
    # d) scopes=None（off 态）任意 repo 透传（既有行为零变更）
    out5 = await wrap_tool(tool, ToolCallTracker()).ainvoke({"repo": "wherever"})
    assert "no permission" not in out5 and '"echo_repo": "wherever"' in out5


# ── API 层：chat repo 门 403 + repos 列表过滤 + graph repo 门 ───────────────
# 终审 I-1：conversations 历史读通道 repo 门（列表过滤 + 详情 404）。

def _override_user(app, user):
    from app.api.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user


def test_chat_repo_forbidden_403(monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "rbac_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret")  # 见模块 docstring 适配
    _override_user(app, {"username": "ext", "role": "external",
                         "allowed_scopes": {"repos": ["sa-token"], "kinds": ["doc"]},
                         "endpoint_classes": ["*"]})
    try:
        with TestClient(app) as client:
            r = client.post("/v1/chat/completions",
                            json={"query": "q", "repo": "rocketmq"})
            assert r.status_code == 403
            # 可见 repo 放行（走到 SSE——钉 LLM 为无 key 避免 ESPN 依赖，断言 200）
            from app.agent import nodes, query_analysis
            monkeypatch.setattr(query_analysis, "configured", lambda: False)
            monkeypatch.setattr(nodes, "configured", lambda: False)
            assert client.post("/v1/chat/completions",
                               json={"query": "q", "repo": "sa-token"}).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_repos_list_filtered(monkeypatch, tmp_path):
    from app.main import app

    monkeypatch.setattr(settings, "rbac_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret")  # 见模块 docstring 适配
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    monkeypatch.setattr("app.api.repos.settings.repos_root", str(tmp_path))
    _override_user(app, {"username": "u", "role": "external",
                         "allowed_scopes": {"repos": ["a"], "kinds": ["doc"]},
                         "endpoint_classes": ["*"]})
    try:
        with TestClient(app) as client:
            assert client.get("/v1/repos").json()["items"] == ["a"]
    finally:
        app.dependency_overrides.clear()


def test_graph_repo_forbidden_403(monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "rbac_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret")  # 见模块 docstring 适配
    _override_user(app, {"username": "u", "role": "developer",
                         "allowed_scopes": {"repos": ["sa-token"], "kinds": ["code", "doc"]},
                         "endpoint_classes": ["*"]})
    try:
        with TestClient(app) as client:
            assert client.get("/v1/graph/search",
                              params={"q": "x", "repo": "rocketmq"}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_conversations_repo_gate(monkeypatch):
    """终审 I-1：conversations 历史读通道补 repo 门。

    external（doc-only、repos=["sa-token"]）用户：不可见 repo（rocketmq）的会话
    不进列表、详情 404（与不存在同判，不暴露存在性）——assistant 正文含
    file:line 代码引用，历史读不得旁路图内门；可见 repo 会话正常列出可读。
    种子走 sync 引擎裸 INSERT（on conflict do update 幂等），测后删净——不碰
    共享 async engine 连接池（TestClient 独立事件循环复用旧池连接会在 pre-ping
    炸，见 test_chat_api 模块 docstring）。
    """
    import uuid

    from sqlalchemy import create_engine, text

    from app.main import app

    monkeypatch.setattr(settings, "rbac_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret")  # 见模块 docstring 适配
    hidden_id, visible_id = uuid.uuid4().hex, uuid.uuid4().hex
    sync_engine = create_engine(settings.postgres_dsn_sync)
    try:
        with sync_engine.connect() as conn:
            for cid, repo in ((hidden_id, "rocketmq"), (visible_id, "sa-token")):
                conn.execute(text(
                    "insert into conversations (id, target_repo, title) values "
                    "(:i, :r, :t) on conflict (id) do update "
                    "set target_repo = excluded.target_repo"),
                    {"i": cid, "r": repo, "t": f"repo 门测试 {repo}"})
            conn.commit()
        _override_user(app, {"username": "ext", "role": "external",
                             "allowed_scopes": {"repos": ["sa-token"], "kinds": ["doc"]},
                             "endpoint_classes": ["*"]})
        with TestClient(app) as client:
            lst = client.get("/v1/chat/conversations").json()
            ids = [c["id"] for c in lst]
            assert visible_id in ids and hidden_id not in ids
            assert all(c["target_repo"] == "sa-token" for c in lst)  # 列表只出可见集
            # 不可见详情 404，且与「不存在」响应不可区分（不暴露存在性）
            assert client.get(f"/v1/chat/conversations/{hidden_id}").status_code == 404
            assert client.get("/v1/chat/conversations/deadbeef").status_code == 404
            detail = client.get(f"/v1/chat/conversations/{visible_id}")
            assert detail.status_code == 200
            assert detail.json()["conversation"]["target_repo"] == "sa-token"
    finally:
        with sync_engine.connect() as conn:
            conn.execute(text("delete from conversations where id = any(:ids)"),
                         {"ids": [hidden_id, visible_id]})
            conn.commit()
        sync_engine.dispose()
        app.dependency_overrides.clear()
