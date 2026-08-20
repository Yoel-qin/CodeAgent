"""HITL 人在回路中断（Phase 7 Milestone 10）单测。

覆盖：
  - ``doc_maintain._pick_stale_candidate`` / ``_apply_stale_mark`` 纯 helper（假 session）。
  - ``propose`` / ``apply_stale`` / ``reject`` 节点（mock recall/LLM/锚点/writer）。
  - ``after_propose`` / ``after_confirm`` 条件路由（纯函数）。
  - ``stream_graph`` 中断检测（假 graph：astream + aget_state → 发 interrupt、落 interrupted 消息、不发 done；
    无中断 → 正常 done 回归）。
  - ``resume_graph``（假 graph：Command(resume=…) 续跑流 token → finalize → done）。

interrupt() 的真实暂停/resume 语义由 e2e 覆盖；此处用 mock 验证「接线 + 中断检测 + 续跑落库」。
"""
from __future__ import annotations

from types import SimpleNamespace

import app.agent.nodes.doc_maintain as dm
import app.agent.streaming as streaming
from app.agent.nodes.doc_maintain import (
    _apply_stale_mark,
    _pick_stale_candidate,
    after_confirm,
    after_propose,
    apply_stale,
    propose,
    reject,
)
from app.agent.streaming import resume_graph, stream_graph
from app.core.config import settings

# ---- 假 session / 结果（记录 execute 的 stmt、commit 计数、返回预置结果）----


class _FakeResult:
    def __init__(self, *, first=None, rowcount=1):
        self._first = first
        self.rowcount = rowcount

    def first(self):
        return self._first


class _FakeSession:
    def __init__(self, result=None):
        self.result = result or _FakeResult()
        self.execute_calls: list = []
        self.commits = 0

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        return self.result

    async def commit(self):
        self.commits += 1


# ---- _pick_stale_candidate ----


async def test_pick_stale_candidate_empty_ranked_short_circuits():
    session = _FakeSession()
    assert await _pick_stale_candidate(session, []) is None
    assert session.execute_calls == []  # 空 ids 短路，未查库


async def test_pick_stale_candidate_maps_row_and_orders_by_confidence():
    row = SimpleNamespace(relation_id=42, anchor_key="Foo.checkLocalTransaction",
                          source_chunk_id="code_x", target_chunk_id="doc_y",
                          relation_type="DOC_TO_CODE")
    session = _FakeSession(_FakeResult(first=row))
    out = await _pick_stale_candidate(session, [{"chunk_id": "code_x", "kind": "code"}])
    assert out == {"relation_id": 42, "anchor_key": "Foo.checkLocalTransaction",
                   "source_chunk_id": "code_x", "target_chunk_id": "doc_y",
                   "relation_type": "DOC_TO_CODE"}
    sql = str(session.execute_calls[0].compile(compile_kwargs={"literal_binds": True}))
    assert "ORDER BY" in sql and "confidence" in sql and "DESC" in sql  # 取 confidence 最高
    assert "is_stale" in sql  # 仅未过时


async def test_pick_stale_candidate_none_when_no_row():
    session = _FakeSession(_FakeResult(first=None))
    assert await _pick_stale_candidate(session, [{"chunk_id": "c1"}]) is None


# ---- _apply_stale_mark ----


async def test_apply_stale_mark_sets_is_stale_and_commits():
    session = _FakeSession(_FakeResult(rowcount=1))
    n = await _apply_stale_mark(session, {"relation_id": 7}, "过时原因")
    assert n == 1
    assert session.commits == 1
    sql = str(session.execute_calls[0].compile(compile_kwargs={"literal_binds": True}))
    assert "UPDATE" in sql and "is_stale" in sql and "true" in sql.lower()
    assert "stale_reason" in sql and "过时原因" in sql  # 写入原因
    assert "7" in sql  # WHERE relation_id = 7


# ---- propose / apply_stale / reject 节点 ----


def _writer_recorder(buf: list):
    """monkeypatch get_stream_writer → 返回一个把事件 append 进 buf 的 writer。"""
    return lambda: lambda d: buf.append(d)


async def test_propose_sets_state_and_emits_retrieval_citation_no_token(monkeypatch):
    ranked = [{"chunk_id": "code_x", "kind": "code", "class_name": "Foo",
               "method_name": "run", "score": 0.9, "content": "..."}]
    meta = {"merged": 1, "recall": {"vector": 1}}

    async def fake_recall(session, query, top_k=8, **kwargs):
        return ranked, meta

    async def fake_enrich(session, rs):
        return None

    async def fake_pick(session, rs):
        return {"relation_id": 5, "anchor_key": "Foo.run", "source_chunk_id": "code_x",
                "target_chunk_id": "doc_y", "relation_type": "DOC_TO_CODE"}

    monkeypatch.setattr(dm, "pipeline", SimpleNamespace(recall=fake_recall))
    monkeypatch.setattr(dm, "_enrich_content_types", fake_enrich)
    monkeypatch.setattr(dm, "_pick_stale_candidate", fake_pick)
    # _draft_proposal 走模板兜底（configured 是只读 property → 打在类上），不触真实 LLM
    monkeypatch.setattr(type(dm.llm), "configured", property(lambda self: False))

    events: list = []
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder(events))

    out = await propose({"query": "这个锚点过时了吗"}, {"configurable": {"session": object(), "top_k": 8}})
    assert out["stale_anchors"][0]["relation_id"] == 5
    assert "Foo.run" in out["proposal"]
    assert out["retrieval_meta"] == meta and out["ranked"] == ranked
    ev_types = [e["event"] for e in events]
    assert "retrieval" in ev_types and "citation" in ev_types
    assert "token" not in ev_types  # 有锚点分支不流 token（避免中断前漏半句）


async def test_propose_no_anchor_emits_token_and_short_circuits(monkeypatch):
    async def fake_recall(session, query, top_k=8, **kwargs):
        return [{"chunk_id": "c1", "kind": "code", "score": 0.5, "content": ""}], {"merged": 1}

    monkeypatch.setattr(dm, "pipeline", SimpleNamespace(recall=fake_recall))
    monkeypatch.setattr(dm, "_enrich_content_types", lambda *a, **k: _noop())
    monkeypatch.setattr(dm, "_pick_stale_candidate", lambda *a, **k: _none())
    monkeypatch.setattr(type(dm.llm), "configured", property(lambda self: False))  # 强制走降级路径
    events: list = []
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder(events))

    out = await propose({"query": "q"}, {"configurable": {"session": object(), "top_k": 8}})
    assert out["proposal"] is None and out["stale_anchors"] == []
    assert any(e["event"] == "token" for e in events)  # 无锚点 → 发说明 token


async def _noop():
    return None


async def _none():
    return None


async def test_apply_stale_node_writes_and_emits_token(monkeypatch):
    written: dict = {}

    async def fake_mark(session, anchor, reason):
        written.update(anchor=anchor, reason=reason)
        return 1

    async def fake_gen(session, *, doc_chunk_id, code_chunk_id):
        return {"rewritten_ok": True, "rewritten_text": "新段落", "original_text": "旧段落",
                "artifact_key": "doc-updates/1/1.md", "file_id": 1, "file_path": "docs/x.md",
                "heading_path": ["指南", "3.2"], "reason": "ok"}

    async def fake_pr(session, **kw):
        return {"proposal_id": 11, "branch_name": "coderag/doc-update-1-x",
                "commit_message": "docs: ...", "status": "PENDING_PUSH",
                "artifact_key": kw.get("artifact_key"), "rewritten_ok": True}

    monkeypatch.setattr(dm, "_apply_stale_mark", fake_mark)
    monkeypatch.setattr(dm, "generate_doc_update", fake_gen)
    monkeypatch.setattr(dm, "create_doc_pr", fake_pr)
    events: list = []
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder(events))

    out = await apply_stale(
        {"stale_anchors": [{"relation_id": 7, "anchor_key": "X",
                            "source_chunk_id": "doc_y", "target_chunk_id": "code_x",
                            "relation_type": "DOC_TO_CODE"}],
         "decision": {"approved": True, "comment": "已过时"}},
        {"configurable": {"session": object()}},
    )
    assert written["anchor"]["relation_id"] == 7 and written["reason"] == "已过时"
    assert out["answer"].startswith("✅")
    assert events and events[0]["event"] == "token"
    # M15：apply 现也产出 PR 提案，摘要 token 带 status
    assert any("PENDING_PUSH" in e["data"]["content"] for e in events if e["event"] == "token")


async def test_reject_node_emits_token_no_write(monkeypatch):
    events: list = []
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder(events))
    out = await reject({})
    assert out["answer"].startswith("❌")
    assert events[0]["data"]["content"].startswith("❌")


# ---- propose ReAct 路径（M13）----


class _FakeReactAgent:
    """假 create_react_agent：astream 按预置 chunks 产出 custom 事件（或抛错）。"""

    def __init__(self, chunks: list[dict] | None = None, exc: Exception | None = None):
        self._chunks = chunks or []
        self._exc = exc

    async def astream(self, messages, config=None, stream_mode=None):
        if self._exc:
            raise self._exc
        for c in self._chunks:
            yield c


async def test_propose_react_captures_proposal_and_bridges_agent_events(monkeypatch):
    chunks = [
        {"event": "agent_step", "data": {"tool": "detect_stale_docs",
                                         "args": {"center": "class:Foo"}, "n": 2}},
        {"event": "citation", "data": {"chunk_id": "code_x", "kind": "code", "score": 0.6}},
        {"event": "_proposal_captured",
         "data": {"summary": "建议将 Foo.run 标记为过时：代码已改而文档未更",
                  "anchors": [{"relation_id": 5, "anchor_key": "Foo.run",
                               "source_chunk_id": "code_x", "target_chunk_id": "doc_y"}],
                  "reason": "代码近期变更"}},
    ]
    monkeypatch.setattr(type(dm.llm), "configured", property(lambda self: True))  # 走 ReAct 路径
    monkeypatch.setattr(dm, "get_doc_maintain_agent", lambda: _FakeReactAgent(chunks))
    events: list = []
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder(events))

    out = await propose({"query": "Foo 的文档过时了吗", "history": []},
                        {"configurable": {"session": object(), "top_k": 8}})
    assert out["proposal"] == "建议将 Foo.run 标记为过时：代码已改而文档未更"
    assert out["stale_anchors"] == [{"relation_id": 5, "anchor_key": "Foo.run",
                                     "source_chunk_id": "code_x", "target_chunk_id": "doc_y"}]
    ev_types = [e["event"] for e in events]
    assert "agent_step" in ev_types and "citation" in ev_types  # 桥接到主图（抽屉可见）
    assert "_proposal_captured" not in ev_types  # 内部协议事件不桥接到 SSE
    assert "token" not in ev_types  # 不流 token（避免中断前漏半句）


async def test_propose_react_no_submission_emits_token_empty_anchors(monkeypatch):
    chunks = [{"event": "agent_step", "data": {"tool": "detect_stale_docs", "args": {}, "n": 0}}]
    monkeypatch.setattr(type(dm.llm), "configured", property(lambda self: True))
    monkeypatch.setattr(dm, "get_doc_maintain_agent", lambda: _FakeReactAgent(chunks))
    events: list = []
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder(events))

    out = await propose({"query": "q", "history": []},
                        {"configurable": {"session": object(), "top_k": 8}})
    assert out["proposal"] is None and out["stale_anchors"] == []
    assert any(e["event"] == "token" for e in events)  # 结论无过时 → 发说明 token


async def test_propose_react_exception_degrades(monkeypatch):
    monkeypatch.setattr(type(dm.llm), "configured", property(lambda self: True))
    monkeypatch.setattr(dm, "get_doc_maintain_agent",
                        lambda: _FakeReactAgent(exc=RuntimeError("boom")))
    events: list = []
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder(events))

    out = await propose({"query": "q", "history": []},
                        {"configurable": {"session": object(), "top_k": 8}})
    assert out["stale_anchors"] == [] and out["proposal"] is None
    assert any(e["event"] == "token" for e in events)  # 异常降级 token，不中断请求


# ---- M41 propose trace 追加 ----


async def test_propose_react_records_agent_span(monkeypatch):
    """M41：collector 存在时 propose 的 ReAct 路径记录 agent span（kind=agent, name=doc_maintain）。"""
    from app.agent.trace import SpanCollector

    collector = SpanCollector()
    chunks = [
        {"event": "agent_step", "data": {"tool": "detect_stale_docs", "args": {}, "n": 0}},
        {"event": "_proposal_captured",
         "data": {"summary": "建议标记过时", "anchors": [{"relation_id": 5, "anchor_key": "X"}],
                  "reason": "代码改了"}},
    ]
    monkeypatch.setattr(type(dm.llm), "configured", property(lambda self: True))
    monkeypatch.setattr(dm, "get_doc_maintain_agent", lambda: _FakeReactAgent(chunks))
    events: list = []
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder(events))

    out = await propose(
        {"query": "Foo 文档过时了吗", "history": []},
        {"configurable": {"session": object(), "top_k": 8, "trace": collector}},
    )
    assert out["proposal"] == "建议标记过时"
    payload = collector.to_payload()
    agent_spans = [s for s in payload["spans"] if s["kind"] == "agent" and s["name"] == "doc_maintain"]
    assert len(agent_spans) >= 1, f"应记录 doc_maintain agent span，实际 spans: {payload['spans']}"
    assert payload["summary"]["kind_counts"].get("agent", 0) >= 1


async def test_propose_react_no_token_leak_with_collector(monkeypatch):
    """M41 终审修复：propose 注入 TraceCallbackHandler(emit_tokens=False) 不得泄漏 token 事件。
    即使 FakeReactAgent 产出了 token 事件（模拟内部 create_react_agent 的 token→SSE），
    TraceCallbackHandler 默认不推送 → SSE 流不应含 token（M15「中断前不漏半句」）。"""
    from app.agent.trace import SpanCollector

    collector = SpanCollector()
    chunks = [
        {"event": "agent_step", "data": {"tool": "detect_stale_docs", "args": {}, "n": 0}},
        {"event": "_proposal_captured",
         "data": {"summary": "建议标记过时", "anchors": [{"relation_id": 5, "anchor_key": "X"}],
                  "reason": "代码改了"}},
    ]
    monkeypatch.setattr(type(dm.llm), "configured", property(lambda self: True))
    monkeypatch.setattr(dm, "get_doc_maintain_agent", lambda: _FakeReactAgent(chunks))
    events: list = []
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder(events))

    out = await propose(
        {"query": "Foo 文档过时了吗", "history": []},
        {"configurable": {"session": object(), "top_k": 8, "trace": collector}},
    )
    assert out["proposal"] == "建议标记过时"
    # agent_step 桥接到 SSE（抽屉可见），但 token 不应出现
    ev_types = [e["event"] for e in events]
    assert "agent_step" in ev_types, "agent_step 应桥接到 SSE"
    assert "token" not in ev_types, \
        f"TraceCallbackHandler emit_tokens=False 不应泄漏 token，实际事件: {ev_types}"


async def test_apply_stale_loops_multi_anchor(monkeypatch):
    mark_calls: list = []
    gen_calls: list = []

    async def fake_mark(session, anchor, reason):
        mark_calls.append(anchor["relation_id"])
        return 1

    async def fake_gen(session, *, doc_chunk_id, code_chunk_id):
        gen_calls.append(doc_chunk_id)
        return {"rewritten_ok": False, "rewritten_text": None, "original_text": None,
                "artifact_key": None, "file_id": 1, "file_path": "d.md",
                "heading_path": [], "reason": "no_llm"}

    async def fake_pr(session, **kw):
        return {"proposal_id": 1, "branch_name": "b", "commit_message": "m",
                "status": "PENDING_MANUAL", "artifact_key": None, "rewritten_ok": False}

    monkeypatch.setattr(dm, "_apply_stale_mark", fake_mark)
    monkeypatch.setattr(dm, "generate_doc_update", fake_gen)
    monkeypatch.setattr(dm, "create_doc_pr", fake_pr)
    events: list = []
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder(events))

    out = await apply_stale(
        {"stale_anchors": [{"relation_id": 7, "anchor_key": "A",
                            "source_chunk_id": "doc_a", "target_chunk_id": "code_a"},
                           {"relation_id": 8, "anchor_key": "B",
                            "source_chunk_id": "doc_b", "target_chunk_id": "code_b"}],
         "decision": {"approved": True, "comment": "过时"}},
        {"configurable": {"session": object()}},
    )
    assert mark_calls == [7, 8]  # 循环标记多个锚点
    assert gen_calls == ["doc_a", "doc_b"]  # 两个不同段落各重写一次
    assert out["answer"].startswith("✅") and "2" in out["answer"]


async def test_apply_stale_dedup_same_doc_chunk_aggregates_relation_ids(monkeypatch):
    """M15：多锚点指向同一文档段落 → 只重写一次、relation_ids 聚合进同一条提案。"""
    gen_calls: list = []
    pr_calls: list = []

    async def fake_mark(session, anchor, reason):
        return 1

    async def fake_gen(session, *, doc_chunk_id, code_chunk_id):
        gen_calls.append(doc_chunk_id)
        return {"rewritten_ok": True, "rewritten_text": "新", "original_text": "旧",
                "artifact_key": "k", "file_id": 2, "file_path": "d.md",
                "heading_path": ["H"], "reason": "ok"}

    async def fake_pr(session, **kw):
        pr_calls.append(kw.get("relation_ids"))
        return {"proposal_id": 9, "branch_name": "b", "commit_message": "m",
                "status": "PENDING_PUSH", "artifact_key": "k", "rewritten_ok": True}

    monkeypatch.setattr(dm, "_apply_stale_mark", fake_mark)
    monkeypatch.setattr(dm, "generate_doc_update", fake_gen)
    monkeypatch.setattr(dm, "create_doc_pr", fake_pr)
    monkeypatch.setattr(dm, "get_stream_writer", _writer_recorder([]))

    out = await apply_stale(
        {"stale_anchors": [
            {"relation_id": 7, "source_chunk_id": "doc_same", "target_chunk_id": "code_a"},
            {"relation_id": 8, "source_chunk_id": "doc_same", "target_chunk_id": "code_b"}],
         "decision": {"approved": True}},
        {"configurable": {"session": object()}},
    )
    assert gen_calls == ["doc_same"]   # 同一段落只重写一次
    assert pr_calls == [[7, 8]]        # 两个 relation_id 聚合进同一条提案
    assert "PENDING_PUSH" in out["answer"]


# ---- 条件路由 ----


def test_after_propose_routes_on_anchor_presence():
    assert after_propose({"stale_anchors": [{"relation_id": 1}]}) == "confirm"
    assert after_propose({"stale_anchors": []}) == "post_process"
    assert after_propose({}) == "post_process"


def test_after_confirm_routes_on_approved():
    assert after_confirm({"decision": {"approved": True}}) == "apply"
    assert after_confirm({"decision": {"approved": False}}) == "reject"
    assert after_confirm({}) == "reject"  # 缺决策 → 安全侧拒绝


# ---- stream_graph 中断检测 ----


class _FakeInterrupt:
    def __init__(self, value):
        self.value = value


class _FakeTask:
    def __init__(self, interrupts):
        self.interrupts = interrupts


class _FakeSnap:
    def __init__(self, values, next=()):
        ints = [_FakeInterrupt(v) for v in values]
        self.tasks = [_FakeTask(ints)]
        self.interrupts = ints
        self.next = next  # 待执行的下一组节点（continue_graph 用：空 → 无可推进）


class _FakeGraph:
    def __init__(self, chunks, snap):
        self._chunks = chunks
        self._snap = snap

    async def astream(self, state, config=None, stream_mode=None):
        for c in self._chunks:
            yield c

    async def aget_state(self, config=None):
        return self._snap


def _patch_stream_graph_deps(monkeypatch, *, chunks, snap, assistant_status_seen: dict):
    conv = SimpleNamespace(title="t", agent_type="DOC_MAINTAIN", conversation_id="conv_1", target_repo=None)

    async def fake_open(session, query, agent_type, cid, *, target_repo=None):
        return conv, "conv_1"

    async def fake_add_user(session, conv, query, agent_type):
        return "msg_u"

    async def fake_history(session, cid, *, exclude_message_id, limit):
        return []

    async def fake_persist(session, query, meta, citations, agent_steps=None):
        return SimpleNamespace(log_id=99)

    async def fake_add_assistant(session, conv, content, citations, rlog_id, agent_type, *, status="completed"):
        assistant_status_seen["status"] = status
        assistant_status_seen["content"] = content
        return "msg_a"

    monkeypatch.setattr(streaming, "open_conversation", fake_open)
    monkeypatch.setattr(streaming, "add_user_message", fake_add_user)
    monkeypatch.setattr(streaming, "load_conversation_history", fake_history)
    monkeypatch.setattr(streaming, "persist_retrieval_log", fake_persist)
    monkeypatch.setattr(streaming, "add_assistant_message", fake_add_assistant)
    monkeypatch.setattr(streaming, "get_graph", lambda: _FakeGraph(chunks, snap))


async def test_stream_graph_interrupted_yields_interrupt_no_done(monkeypatch):
    seen: dict = {}
    _patch_stream_graph_deps(
        monkeypatch,
        chunks=[{"event": "retrieval", "data": {"merged": 2}}],
        snap=_FakeSnap(["建议将锚点 Foo.run 标记为过时"]),
        assistant_status_seen=seen,
    )
    out = [x async for x in stream_graph(object(), "q", agent_type="DOC_MAINTAIN", conversation_id="conv_1")]
    events = [e for e, _ in out]
    assert "interrupt" in events and "done" not in events
    interrupt_data = next(d for e, d in out if e == "interrupt")
    assert interrupt_data["proposal"] == "建议将锚点 Foo.run 标记为过时"
    assert interrupt_data["message_id"] == "msg_a" and interrupt_data["conversation_id"] == "conv_1"
    assert seen["status"] == "interrupted"  # 落中断态消息


async def test_stream_graph_normal_completion_yields_done(monkeypatch):
    seen: dict = {}
    _patch_stream_graph_deps(
        monkeypatch,
        chunks=[{"event": "token", "data": {"content": "完成"}}],
        snap=_FakeSnap([]),  # 无中断 → 正常完成
        assistant_status_seen=seen,
    )
    out = [x async for x in stream_graph(object(), "q", conversation_id="conv_1")]
    events = [e for e, _ in out]
    assert "done" in events and "interrupt" not in events
    assert seen["status"] == "completed"  # 正常完成态


# ---- resume_graph ----


async def test_resume_graph_streams_tokens_finalizes_and_done(monkeypatch):
    finalize_calls: list = []

    async def fake_finalize(session, message_id, content):
        finalize_calls.append((message_id, content))

    monkeypatch.setattr(streaming, "finalize_interrupted_message", fake_finalize)
    monkeypatch.setattr(streaming, "get_graph", lambda: _FakeGraph(
        chunks=[{"event": "token", "data": {"content": "✅ "}},
                {"event": "token", "data": {"content": "已标记过时"}}],
        snap=None,
    ))

    out = [x async for x in resume_graph(
        object(), conversation_id="conv_1", message_id="msg_a", decision={"approved": True})]
    events = [e for e, _ in out]
    assert events == ["token", "token", "done"]
    assert finalize_calls == [("msg_a", "✅ 已标记过时")]
    assert out[-1] == ("done", {"message_id": "msg_a", "conversation_id": "conv_1"})


# ---- continue_graph / GET /state（M14 Part C）----


class _Scalars:
    def __init__(self, val):
        self._val = val

    def first(self):
        return self._val


class _ExecResult:
    def __init__(self, val):
        self._val = val

    def scalars(self):
        return _Scalars(self._val)


class _StateSession:
    """``get_thread_state`` 用：第 1 次 execute 返 latest assistant；第 2 次（中断分支）返 interrupted 消息。"""

    def __init__(self, latest, interrupted):
        self._latest = latest
        self._interrupted = interrupted
        self.calls = 0

    async def execute(self, stmt):
        self.calls += 1
        return _ExecResult(self._latest if self.calls == 1 else self._interrupted)


class _ContinueSession:
    """``continue_graph`` 用：``.get(Conversation, cid)`` 返 conv（建消息分支才用）。"""

    def __init__(self, conv):
        self._conv = conv

    async def get(self, model, pk):
        return self._conv


async def test_continue_graph_pending_interrupt_reports_without_advancing(monkeypatch):
    # snap 有 interrupt → 发 interrupt 事件；astream(None) 不应被消费（chunks 里的 token 不应出现）
    snap = _FakeSnap(["提案：标记过时"])
    graph = _FakeGraph(chunks=[{"event": "token", "data": {"content": "不应出现"}}], snap=snap)
    monkeypatch.setattr(streaming, "get_graph", lambda: graph)
    out = [x async for x in streaming.continue_graph(
        object(), conversation_id="conv_1", message_id="msg_a")]
    events = [e for e, _ in out]
    assert "interrupt" in events and "done" not in events and "token" not in events
    data = next(d for e, d in out if e == "interrupt")
    assert data["proposal"] == "提案：标记过时" and data["message_id"] == "msg_a"


async def test_continue_graph_no_interrupt_advances_and_creates_message(monkeypatch):
    snap = _FakeSnap([], next=("generate",))  # 有 pending 节点 → astream(None) 推进
    graph = _FakeGraph(chunks=[{"event": "token", "data": {"content": "恢复的答案"}}], snap=snap)
    monkeypatch.setattr(streaming, "get_graph", lambda: graph)
    added: dict = {}

    async def fake_add(session, conv, content, citations, rlog_id, agent_type, *, status="completed"):
        added.update(content=content, status=status)
        return "msg_new"

    monkeypatch.setattr(streaming, "add_assistant_message", fake_add)
    conv = SimpleNamespace(conversation_id="conv_1", message_count=0)
    out = [x async for x in streaming.continue_graph(
        _ContinueSession(conv), conversation_id="conv_1")]
    events = [e for e, _ in out]
    assert "token" in events and "done" in events
    assert added["content"] == "恢复的答案" and added["status"] == "completed"
    assert next(d for e, d in out if e == "done")["message_id"] == "msg_new"


async def test_continue_graph_finished_thread_yields_noop_done(monkeypatch):
    snap = _FakeSnap([], next=())  # 无 pending 节点 → noop（不调 astream(None)，避免 EmptyInputError）
    graph = _FakeGraph(chunks=[], snap=snap)
    monkeypatch.setattr(streaming, "get_graph", lambda: graph)
    out = [x async for x in streaming.continue_graph(object(), conversation_id="conv_1")]
    assert [e for e, _ in out] == ["done"]
    assert out[0][1]["message_id"] is None  # noop，不建消息


async def test_get_thread_state_with_pending_interrupt(monkeypatch):
    from datetime import UTC, datetime, timedelta

    import app.agent.graph as graph_mod
    from app.api.v1.conversations import get_thread_state

    monkeypatch.setattr(settings, "rag_engine", "langgraph")
    snap = _FakeSnap(["提案：标记过时"])
    monkeypatch.setattr(graph_mod, "get_graph", lambda: _FakeGraph(chunks=[], snap=snap))
    imsg = SimpleNamespace(
        message_id="msg_i", created_at=datetime.now(UTC) - timedelta(hours=3),
    )
    out = await get_thread_state(
        "conv_1", _StateSession(latest=SimpleNamespace(status="interrupted"), interrupted=imsg),
    )
    assert out.has_pending_interrupt is True
    assert out.status == "interrupted"
    assert out.interrupt is not None
    assert out.interrupt.proposal == "提案：标记过时"
    assert out.interrupt.message_id == "msg_i"
    assert out.interrupt.age_hours is not None and out.interrupt.age_hours >= 2.9


async def test_get_thread_state_no_interrupt(monkeypatch):
    import app.agent.graph as graph_mod
    from app.api.v1.conversations import get_thread_state

    monkeypatch.setattr(settings, "rag_engine", "langgraph")
    monkeypatch.setattr(graph_mod, "get_graph", lambda: _FakeGraph(chunks=[], snap=_FakeSnap([])))
    out = await get_thread_state(
        "conv_1", _StateSession(latest=SimpleNamespace(status="completed"), interrupted=None),
    )
    assert out.has_pending_interrupt is False
    assert out.interrupt is None
    assert out.status == "completed"
