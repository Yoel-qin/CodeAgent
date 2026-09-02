"""Task 9：图装配 + streaming + SSE 契约 + 会话端点测试。

brief 3 个逐字 + 3 个补充（① query_analysis LLM 路成功 → docqa 全链 ② conversation_id
非法 422 ③ done 事件与 PG 落行一致）。真 PG（会话/消息真落库并复核）；MCP / LLM /
doc IO 全 monkeypatch 为本地 fake——不打真 MCP/LLM。

三处与 brief 逐字文本的适配（其余逐行一致）：
1. brief 测试里补一行 ``monkeypatch.setattr(nodes, "configured", lambda: False)``——
   brief 测试头注明「不打真 LLM」，但其只钉了 ``query_analysis.configured``；本机
   backend-v2/.env 与根 .env 均有真 key，``nodes.configured``（真实实现读 settings）会
   翻 True → retrieve/clarify 误入真 DeepSeek 网络调用。CI 无 key 本就 False，加钉不改变
   CI 行为，只消除本机网络依赖。
2. 模块级 autouse fixture：① 测后删**本测新建**的会话行——本文件走真 PG 真提交（brief
   契约），遗留行会破坏既有 ``test_chat_service`` 排序测试对「表里只有本测两行」的假设；
   ② TestClient 每个上下文起**独立事件循环**，app 共享 engine 的池化 asyncpg 连接绑在旧
   循环上，跨测试复用会在 pre-ping 处炸（本地实测复现）——每测后 ``engine.dispose()``
   清池，下个测试新建连接。
3. brief 只钉 ``tools_loader.load_tools``，但 ``app/main.py`` 是
   ``from app.agent.tools_loader import load_tools`` **直引符号**——不钉 ``app.main.load_tools``
   则 lifespan 照样真连 MCP（本地 graph-mcp 活着 → 真工具 → 真 DeepSeek ReAct，单测 80s+）。
   两处都钉才真正阻断。
"""
import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app.core.config import settings
from app.db.base import engine


@pytest.fixture(autouse=True)
def _dispose_engine_per_test():
    """测后清场：删本测新建的会话行 + 清掉绑在已关闭事件循环上的池化连接。

    - **行清理**：本文件走真 PG 真提交（brief 契约），遗留行会破坏 ``test_chat_service``
      对 ``list_conversations``「表里只有本测两行」的既有假设——只删**本测期间新增**的
      conversation（chat_messages 随 ondelete=CASCADE 级联），不动库里的任何既有数据。
    - **连接池清理**：TestClient 每个上下文新事件循环，app 共享 engine 的池化 asyncpg
      连接绑在旧循环上，跨测试复用会在 pre-ping 处炸——``engine.dispose()`` 清池（旧连接
      关闭发生在另一循环上，sqlalchemy 记 ERROR 日志，临时抬高 logger 级别压掉）。
    """
    from sqlalchemy import create_engine, text

    sync_engine = create_engine(settings.postgres_dsn_sync)
    try:
        with sync_engine.connect() as conn:
            before = {r[0] for r in conn.execute(text("select id from conversations"))}
        yield
        with sync_engine.connect() as conn:
            created = [r[0] for r in conn.execute(text("select id from conversations"))
                       if r[0] not in before]
            if created:
                conn.execute(text("delete from conversations where id = any(:ids)"),
                             {"ids": created})
                conn.commit()
    finally:
        sync_engine.dispose()
        slog = logging.getLogger("sqlalchemy")
        prev, slog.level = slog.level, logging.CRITICAL + 1
        try:
            asyncio.run(engine.dispose())
        finally:
            slog.setLevel(prev)


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    out, ev = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:") and ev:
            out.append((ev, json.loads(line[5:].strip())))
    return out


def _read_sse(resp) -> list[tuple[str, dict]]:
    """流式响应读全量再解析（httpx 流式上下文里不 read 直接取 .text 会 ResponseNotRead）。"""
    resp.read()
    return _parse_sse(resp.text)


# ── brief 逐字测试（含 docstring 第 1 点的一行适配） ─────────────────────────


def test_chat_sse_contract(monkeypatch):
    from app.agent import nodes, query_analysis, tools_loader
    from app.main import app

    async def _noop_load():
        return None
    # main.py 是 ``from app.agent.tools_loader import load_tools`` 直引符号——只钉
    # tools_loader.load_tools 阻断不了 lifespan 真连 MCP（brief 靶点偏差），两处都钉
    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    monkeypatch.setattr("app.main.load_tools", _noop_load)  # 阻断 lifespan 真连 MCP
    monkeypatch.setattr(query_analysis, "configured", lambda: False)
    # 规则分类把 "CommitLog putMessage" 判 code → codenav → 无 key 降级 retrieve；此处给检索路 canned 结果保证 citation ≥1
    monkeypatch.setattr(nodes, "grep_code",
                        lambda *a: {"matches": [{"file": "a/CommitLog.java", "line": 10,
                                                 "content": "putMessage"}],
                                    "total_count": 1, "truncated": False, "engine": "python"})
    monkeypatch.setattr(tools_loader, "get_code_tools", lambda: [])
    monkeypatch.setattr(nodes, "configured", lambda: False)  # 见模块 docstring 第 1 点
    with TestClient(app) as client:
        with client.stream("POST", "/v1/chat/completions",
                           json={"query": "CommitLog putMessage", "repo": "mini"}) as resp:
            assert resp.status_code == 200
            events = _read_sse(resp)
    names = [e for e, _ in events]
    assert names[0] == "conversation"
    assert names[-1] == "done"
    assert "retrieval" in names and "citation" in names and "token" in names
    conv = events[0][1]
    assert set(conv) == {"conversation_id", "title", "message_id"}
    done = events[-1][1]
    assert done["citations"] >= 1 and done["message_id"] and done["conversation_id"] == conv["conversation_id"]


def test_chat_empty_query_400():
    from app.main import app
    with TestClient(app) as client:
        assert client.post("/v1/chat/completions", json={"query": "  "}).status_code == 400


def test_conversations_listed_after_chat(monkeypatch):
    from app.agent import nodes, query_analysis, tools_loader
    from app.main import app

    async def _noop_load():
        return None
    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)  # 两处都钉，理由见 test_chat_sse_contract
    monkeypatch.setattr("app.main.load_tools", _noop_load)
    monkeypatch.setattr(query_analysis, "configured", lambda: False)
    monkeypatch.setattr(nodes, "configured", lambda: False)  # 见模块 docstring 第 1 点
    monkeypatch.setattr(nodes, "grep_code",
                        lambda *a: {"matches": [], "total_count": 0, "truncated": False,
                                    "engine": "python"})
    monkeypatch.setattr(tools_loader, "get_code_tools", lambda: [])
    with TestClient(app) as client:
        with client.stream("POST", "/v1/chat/completions", json={"query": "conv 列表测试"}) as r:
            assert r.status_code == 200
        lst = client.get("/v1/chat/conversations").json()
        assert any("conv 列表测试" in c["title"] for c in lst)


# ── 补充①（Task 6 遗留）：query_analysis LLM 路成功 → docqa 全链 ──────────────


class _FakeToolModel(GenericFakeChatModel):
    """GenericFakeChatModel + bind_tools 返自身（同 test_react_nodes：基类 NotImplementedError）。"""

    def bind_tools(self, tools, **kwargs):
        return self


def test_chat_llm_routes_to_docqa_and_streams(monkeypatch):
    """routing 档结构化分类成功（doc, 0.85）→ 图走 docqa 边 → ReAct 流式回答。

    - retrieval 事件 mode == "docqa" 证明 conditional edge 按 state["route"] 生效；
    - 该事件的 intent/confidence 来自 state 写回，证明 router 节点确实驱动了图状态；
    - 模型不调工具直接作答 → token 流 + docqa 无引用拒答提示 + done(citations=0)。
    """
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import StructuredTool

    from app.agent import docqa, nodes, query_analysis, react_base, tools_loader
    from app.agent.query_analysis import RouteDecision
    from app.main import app

    async def _noop_load():
        return None
    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)  # 两处都钉，理由见 test_chat_sse_contract
    monkeypatch.setattr("app.main.load_tools", _noop_load)
    monkeypatch.setattr(query_analysis, "configured", lambda: True)

    class _StubRoutingModel:
        def with_structured_output(self, _schema):
            return RunnableLambda(lambda _msgs: RouteDecision(intent="doc", confidence=0.85))

    monkeypatch.setattr(query_analysis, "chat_model_for", lambda _t="routing": _StubRoutingModel())
    monkeypatch.setattr(react_base, "configured", lambda: True)
    monkeypatch.setattr(react_base, "chat_model_for",
                        lambda _t="reasoning": _FakeToolModel(
                            messages=iter([AIMessage(content="文档里写明同步刷盘")])))

    async def _no_tool(**_kw):
        return json.dumps({"results": []}, ensure_ascii=False)

    _doc_tool = StructuredTool(
        name="doc_hybrid_search", description="t",
        args_schema={"type": "object", "properties": {}, "additionalProperties": True},
        coroutine=_no_tool)
    monkeypatch.setattr(docqa, "get_doc_tools", lambda: [_doc_tool])
    # 兜底链钉死外部 IO（本测试主链在 docqa ReAct，retrieve 不应被触达）
    monkeypatch.setattr(nodes, "hybrid_search", lambda *a: {"results": [], "recall": 0})
    monkeypatch.setattr(nodes, "grep_code",
                        lambda *a: {"matches": [], "total_count": 0, "truncated": False,
                                    "engine": "python"})

    with TestClient(app) as client:
        with client.stream("POST", "/v1/chat/completions",
                           json={"query": "刷盘机制文档里怎么写的", "top_k": 4}) as resp:
            assert resp.status_code == 200
            events = _read_sse(resp)
    names = [e for e, _ in events]
    assert names[0] == "conversation" and names[-1] == "done"
    retrieval = next(d for e, d in events if e == "retrieval")
    assert retrieval["mode"] == "docqa"
    assert retrieval["intent"] == "doc" and retrieval["confidence"] == 0.85
    answer = "".join(d.get("content", "") for e, d in events if e == "token")
    assert "同步刷盘" in answer and "未找到可引用的文档依据" in answer
    assert events[-1][1]["citations"] == 0


# ── 补充②③：参数校验 + 落库一致性 ────────────────────────────────────────────


def test_chat_invalid_conversation_id_422(monkeypatch):
    """conversation_id 非 32 位 hex → 422（API 边界防脏值落 PG，Task 2 评审遗留）。"""
    from app.agent import tools_loader
    from app.main import app

    async def _noop_load():
        return None
    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)  # 两处都钉，理由见 test_chat_sse_contract
    monkeypatch.setattr("app.main.load_tools", _noop_load)
    with TestClient(app) as client:
        resp = client.post("/v1/chat/completions",
                           json={"query": "q", "conversation_id": "not-a-hex-id"})
    assert resp.status_code == 422


def test_done_ids_match_persisted_rows(monkeypatch):
    """done 事件的 message_id/conversation_id 与 PG 真落行一致 + assistant meta 契约。"""
    from sqlalchemy import create_engine, text

    from app.agent import nodes, query_analysis, tools_loader
    from app.main import app

    async def _noop_load():
        return None
    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)  # 两处都钉，理由见 test_chat_sse_contract
    monkeypatch.setattr("app.main.load_tools", _noop_load)
    monkeypatch.setattr(query_analysis, "configured", lambda: False)
    monkeypatch.setattr(nodes, "configured", lambda: False)  # clarify 走模板，不触 LLM

    with TestClient(app) as client:
        with client.stream("POST", "/v1/chat/completions",
                           json={"query": "落库一致性测试"}) as resp:
            assert resp.status_code == 200
            events = _read_sse(resp)
    conv_ev, done = events[0][1], events[-1][1]
    sync_engine = create_engine(settings.postgres_dsn_sync)
    try:
        with sync_engine.connect() as conn:
            conv = conn.execute(
                text("select id, target_repo from conversations where id = :i"),
                {"i": conv_ev["conversation_id"]}).first()
            msgs = conn.execute(
                text("select id, role, content, meta from chat_messages "
                     "where conversation_id = :i order by id"),
                {"i": conv_ev["conversation_id"]}).all()
    finally:
        sync_engine.dispose()
    assert conv is not None and conv[1] == settings.default_repo
    assert [m[1] for m in msgs] == ["user", "assistant"]
    assert msgs[0][2] == "落库一致性测试"
    assert done["conversation_id"] == conv[0]
    assert done["message_id"] == msgs[1][0]
    meta = msgs[1][3]
    assert {"citations", "agent_steps", "intent", "route", "cost"} <= set(meta)
    assert meta["route"] == "clarify" and set(meta["cost"]) >= {"spent_tokens", "llm_calls", "estimated"}


# ── R1（评审 F-A）：history 载入先于本测 user 落行，当前轮不得泄入 history ───────


def test_history_excludes_current_turn_query(monkeypatch):
    """两轮会话：第二轮节点收到的 history == [user:第一问, assistant:回复] 恰 2 条。

    评审实证的缺陷序（先 add_message(user) 再 load_history）会把当前 query 泄入
    history，retrieve/react_base 的 seed 再追加一次 → 连续两条相同 HumanMessage。
    钉住正确序：第二轮 state["history"] 恰含上一轮一问一答、不含当前 query。
    """
    from langchain_core.runnables import RunnableLambda

    from app.agent import docqa, nodes, query_analysis, react_base, tools_loader
    from app.agent.query_analysis import RouteDecision
    from app.main import app

    async def _noop_load():
        return None
    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)  # 两处都钉，理由见 test_chat_sse_contract
    monkeypatch.setattr("app.main.load_tools", _noop_load)

    # 第一轮：无 key 规则路 → clarify 模板回复（落 user + assistant 两行）
    monkeypatch.setattr(query_analysis, "configured", lambda: False)
    monkeypatch.setattr(nodes, "configured", lambda: False)
    with TestClient(app) as client:
        with client.stream("POST", "/v1/chat/completions",
                           json={"query": "第一问问的是什么"}) as resp:
            turn1 = _read_sse(resp)
        cid = turn1[0][1]["conversation_id"]

        # 第二轮：LLM 路 → docqa → 工具挂 → react_base 转retrieve_node（捕获 state）
        monkeypatch.setattr(query_analysis, "configured", lambda: True)

        class _StubRoutingModel:
            def with_structured_output(self, _schema):
                return RunnableLambda(lambda _msgs: RouteDecision(intent="doc", confidence=0.85))
        monkeypatch.setattr(query_analysis, "chat_model_for", lambda _t="routing": _StubRoutingModel())
        monkeypatch.setattr(docqa, "get_doc_tools", lambda: [])
        captured = {}

        async def _capture_retrieve(state, config):
            captured["history"] = list(state.get("history") or [])
        monkeypatch.setattr(react_base, "retrieve_node", _capture_retrieve)

        with client.stream("POST", "/v1/chat/completions",
                           json={"query": "第二问问的是什么", "conversation_id": cid}) as resp:
            assert resp.status_code == 200
            _read_sse(resp)

    history = captured["history"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "第一问问的是什么"
    assert history[1]["content"]  # 上一轮确有 assistant 回复（clarify 模板）
    assert all("第二问" not in m["content"] for m in history)  # 当前轮不得泄入


def test_load_history_keeps_complete_pairs():
    """load_history 窗口只收完整 user/assistant 对：截断切开一对 → 丢弃开头孤儿 assistant。"""
    import asyncio

    from sqlalchemy import text

    from app.db.base import SessionLocal
    from app.services.chat_service import add_message, load_history, open_conversation

    async def main():
        async with SessionLocal() as session:
            conv, cid = await open_conversation(session, query="u1", conversation_id=None,
                                                target_repo="r")
            try:
                for role, content in (("user", "u1"), ("assistant", "a1"),
                                      ("user", "u2"), ("assistant", "a2"),
                                      ("user", "u3")):  # 末轮无回复
                    await add_message(session, conv, role=role, content=content)
                # 窗口 = 最近 4 条 [a1,u2,a2,u3]：切开了 (u1,a1) → 丢弃开头的孤儿 a1
                history = await load_history(session, cid, 2)
            finally:
                await session.execute(
                    text("delete from conversations where id = :i"), {"i": cid})
                await session.commit()
        return history

    history = asyncio.run(main())
    assert history == [{"role": "user", "content": "u2"},
                       {"role": "assistant", "content": "a2"},
                       {"role": "user", "content": "u3"}]
