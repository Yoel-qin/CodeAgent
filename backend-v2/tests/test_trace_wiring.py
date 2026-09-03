"""Task 9：trace 接线。四层各一单测（直调、trace 注入 config）+ TestClient 集成
（docqa 全链 → trace_spans 真落行 + span kinds 齐全）。真提交测试自清（test_chat_api 模式）。

四处与 brief 逐字文本的适配（沿 test_chat_api 模块 docstring 的既有先例；均为 brief
测试夹具与 langchain-core 1.6.1 / sse-starlette 实际行为的不符，接线契约本身未动）：
1. ``test_query_analysis_records_route_span`` 补 ``monkeypatch.setattr(qa, "configured",
   lambda: False)``——brief 未钉；本机根 .env 有真 key，不钉会打真 DeepSeek（3s 超时后
   转规则），分类结果随网络波动 → 断言 ``route == "codenav"`` 不稳。CI 无 key 本就
   False，钉住只消除本机网络依赖，不改判定路径。
2. ``test_docqa_chat_writes_trace_row`` 的 fake 模型首条消息补 ``tool_calls``——brief 给的
   两条均纯 content AIMessage，``GenericFakeChatModel`` 不会自发调工具（实测
   create_react_agent 首答无 tool_calls 即 END）→ 工具永不执行 → ``tool`` span 不可能
   存在，kinds 断言必挂。按 test_react_nodes 既有模式把首条改为 tool_call 消息、次条
   为终答（工具名/返回值仍用 brief 原文）。
3. 同测试的 done 事件正则 ``event: done\ndata:`` → ``event: done\r?\ndata:``——
   sse-starlette（app/api/chat.py 的 EventSourceResponse）按 SSE 规范写 ``\r\n`` 行尾
   （test_chat_api._parse_sse 的逐行 strip 天然兼容，brief 的正则不兼容），`\r?\n`
   两种行尾都吃。
4. ``test_cost_callback_records_llm_span`` 的 ``ChatResult`` → ``LLMResult``（嵌套
   generations）——langchain 回调机制给 ``on_llm_end`` 传的是 ``LLMResult``（chat_models.py
   全部 ``run_manager.on_llm_end(LLMResult(generations=[[generation]]))``）；langchain-core
   1.6.1 的 ``ChatResult.generations`` 是**扁平** list，brief 的容器到了 handler 里
   ``generations[0][0]`` 下标即 TypeError 被吞 → usage/文本全读不到，断言必挂。
"""
import json
import logging
import re

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool

from app.agent.trace import SpanCollector
from app.core.config import settings
from app.db.base import engine


@pytest.fixture(autouse=True)
def _cleanup():
    from sqlalchemy import create_engine, text

    sync_engine = create_engine(settings.postgres_dsn_sync)
    try:
        with sync_engine.connect() as conn:
            before = {r[0] for r in conn.execute(text("select id from conversations"))}
        yield
        import asyncio

        with sync_engine.connect() as conn:
            created = [r[0] for r in conn.execute(text("select id from conversations"))
                       if r[0] not in before]
            if created:
                conn.execute(text("delete from conversations where id = any(:ids)"),
                             {"ids": created})
                conn.commit()
        sync_engine.dispose()
        slog = logging.getLogger("sqlalchemy")
        prev, slog.level = slog.level, logging.CRITICAL + 1
        try:
            asyncio.run(engine.dispose())
        finally:
            slog.setLevel(prev)
    finally:
        sync_engine.dispose()


async def test_query_analysis_records_route_span(monkeypatch):
    from app.agent import query_analysis as qa

    monkeypatch.setattr(qa, "configured", lambda: False)  # 见模块 docstring 第 1 点
    trace = SpanCollector()
    state = {"query": "CommitLog putMessage"}
    out = await qa.query_analysis_node(state, {"configurable": {"trace": trace}})
    spans = trace.to_dict()
    assert len(spans) == 1 and spans[0]["kind"] == "route"
    assert spans[0]["attrs"]["route"] == out["route"] == "codenav"


async def test_wrap_tool_records_tool_span():
    from app.agent.tools_loader import ToolCallTracker, wrap_tool

    async def _grep(**kw):
        return json.dumps({"matches": [{"file": "a/A.java", "line": 1, "content": "x"}]})

    tool = StructuredTool(name="grep_code", description="d",
                          args_schema={"type": "object", "properties": {}, "additionalProps": True},
                          coroutine=_grep)
    trace = SpanCollector()
    tracker = ToolCallTracker()
    wrapped = wrap_tool(tool, tracker, default_repo=None, trace=trace)
    out = await wrapped.ainvoke({"pattern": "x"})
    assert json.loads(out)["matches"]
    spans = {s["kind"]: s for s in trace.to_dict()}
    assert spans["tool"]["name"] == "grep_code" and spans["tool"]["status"] == "ok"
    # agent span 由 react_base 全程包（下条集成测覆盖）；此处只验 wrap_tool 层


def test_cost_callback_records_llm_span():
    from langchain_core.outputs import ChatGeneration, LLMResult

    from app.agent.callbacks import CostCallbackHandler
    from app.agent.cost import CostController

    cost = CostController(max_tokens=1000, max_llm_calls=5)
    trace = SpanCollector()
    h = CostCallbackHandler(cost, trace=trace)
    h.on_chat_model_start({}, [], run_id="r1")
    msg = AIMessage(content="hello")
    msg.usage_metadata = {"prompt_tokens": 3, "completion_tokens": 2}
    # LLMResult（嵌套 generations）= langchain 回调机制实际传给 on_llm_end 的容器（见模块 docstring 第 4 点）
    h.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id="r1")
    s = trace.to_dict()[0]
    assert s["kind"] == "llm" and s["tokens"] == {"prompt": 3, "completion": 2, "estimated": False}


def test_docqa_chat_writes_trace_row(monkeypatch):
    """docqa 全链（stub routing → ReAct 一工具一直答）→ trace_spans 行落库、kinds 齐全。"""
    from sqlalchemy import create_engine, text

    from app.agent import docqa, nodes, query_analysis, react_base, tools_loader
    from app.agent.query_analysis import RouteDecision
    from app.main import app

    async def _noop_load(transports=None):
        return None

    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    monkeypatch.setattr(query_analysis, "configured", lambda: True)

    class _StubRoutingModel:
        def with_structured_output(self, _schema, method=None):
            return RunnableLambda(lambda _m: RouteDecision(intent="doc", confidence=0.85))

    monkeypatch.setattr(query_analysis, "chat_model_for", lambda _t="routing": _StubRoutingModel())
    monkeypatch.setattr(react_base, "configured", lambda: True)

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    class _Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kw):
            return self

    monkeypatch.setattr(react_base, "chat_model_for",
                        lambda _t="reasoning": _Model(
                            messages=iter([
                                # 首调工具（见模块 docstring 第 2 点适配）→ doc_hybrid_search
                                AIMessage(content="", tool_calls=[{
                                    "name": "doc_hybrid_search", "args": {"query": "刷盘"},
                                    "id": "c1"}]),
                                AIMessage(content="文档答完了")])))

    monkeypatch.setattr(nodes, "hybrid_search", lambda *a: {"results": [], "recall": 0})
    monkeypatch.setattr(nodes, "grep_code",
                        lambda *a: {"matches": [], "total_count": 0, "truncated": False, "engine": "python"})

    async def _tool(**_kw):
        return json.dumps({"results": [{"doc_name": "d.md", "anchor": "s-1",
                                        "title": "T", "score": 0.9}]}, ensure_ascii=False)

    monkeypatch.setattr(docqa, "get_doc_tools", lambda: [StructuredTool(
        name="doc_hybrid_search", description="t",
        args_schema={"type": "object", "properties": {}, "additionalProps": True},
        coroutine=_tool)])

    with TestClient(app) as client:
        with client.stream("POST", "/v1/chat/completions",
                           json={"query": "刷盘文档怎么写"}) as resp:
            assert resp.status_code == 200
            resp.read()
            done = None
            for m in re.finditer(r"event: done\r?\ndata: (.+)", resp.text):
                done = json.loads(m.group(1))
    assert done and done["message_id"]
    eng = create_engine(settings.postgres_dsn_sync)
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "select spans, route, duration_ms, token_usage from trace_spans"
                " where message_id = :i"), {"i": done["message_id"]}).first()
    finally:
        eng.dispose()
    assert row is not None, "trace_spans 必须随 assistant 消息落库"
    spans = row[0]
    kinds = {s["kind"] for s in spans}
    assert {"request", "route", "agent", "tool", "llm"} <= kinds
    # 修复轮：parent_id 自动嵌套（采集器按 open 栈缺省父）——request 为根，
    # route/agent 挂 request，tool/llm 挂 agent（按 span_id 解析，不按位序）
    req_span = next(s for s in spans if s["kind"] == "request")
    route_span = next(s for s in spans if s["kind"] == "route")
    agent_span = next(s for s in spans if s["kind"] == "agent")
    assert req_span["parent_id"] is None
    assert route_span["parent_id"] == req_span["span_id"]
    assert agent_span["parent_id"] == req_span["span_id"]
    assert all(s["parent_id"] == agent_span["span_id"]
               for s in spans if s["kind"] in ("tool", "llm"))
    assert row[1] == "docqa" and row[2] >= 0
    assert set(row[3]) >= {"spent_tokens", "llm_calls"}
