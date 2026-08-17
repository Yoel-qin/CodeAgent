"""会话/消息 落库相关纯函数单元测试（无外部依赖）。"""
from __future__ import annotations

from app.api.v1.conversations import _build_agent_trace
from app.core.ids import prefixed_id
from app.services.chat_service import _derive_title, persist_retrieval_log


def test_prefixed_id_format_and_uniqueness():
    a = prefixed_id("conv")
    b = prefixed_id("msg")
    assert a.startswith("conv_") and len(a) > len("conv_")
    assert b.startswith("msg_")
    assert a != prefixed_id("conv")  # 随机不可重复


def test_derive_title_truncates_long_query():
    long = "请详细解释事务消息的回查机制是如何实现的" * 5  # 远超 40 字
    title = _derive_title(long)
    assert title.endswith("…")
    assert len(title) == 41  # 40 + 省略号


def test_derive_title_short_query_kept_as_is():
    q = "checkLocalTransaction 是做什么的"
    assert _derive_title(q) == q


def test_derive_title_collapses_newlines():
    assert _derive_title("a\nb\nc") == "a b c"


# ---- Agent 步骤可观测性（M5）：agent_steps 持久化 + 回放 ----


class _AddRecorder:
    """假 AsyncSession：记录 add(...) 调用，flush 为 no-op（无需真实 DB）。"""

    last_rlog_agent_steps: dict | list | None = None  # M41：flush 时捕获最新 agent_steps
    flush_history: list[dict | list | None] = []  # M41：每次 flush 的 agent_steps 快照

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        # M41：记录最后一条带 agent_steps 属性的对象（RetrievalLog）
        for obj in reversed(self.added):
            if hasattr(obj, "agent_steps"):
                _AddRecorder.last_rlog_agent_steps = obj.agent_steps
                _AddRecorder.flush_history.append(obj.agent_steps)
                break
        return None

    async def commit(self) -> None:
        return None


async def test_persist_retrieval_log_stores_agent_steps():
    session = _AddRecorder()
    steps = [{"tool": "search_symbol", "args": {"q": "Foo.bar"}, "n": 3}]
    await persist_retrieval_log(session, "q", {}, [], agent_steps=steps)
    assert len(session.added) == 1
    assert session.added[0].agent_steps == steps


async def test_persist_retrieval_log_agent_steps_null_when_absent():
    # legacy/retrieve 路径不传 agent_steps → 列应为 NULL
    session = _AddRecorder()
    await persist_retrieval_log(session, "q", {}, [])
    assert session.added[0].agent_steps is None


def test_build_agent_trace_present():
    steps = [{"tool": "read_code", "args": {"chunk_id": "c1"}, "n": 1}]
    resp = _build_agent_trace(agent_steps=steps, agent_type="CHANGE_IMPACT")
    assert resp is not None
    assert resp.type == "CHANGE_IMPACT"
    assert resp.steps == steps


def test_build_agent_trace_absent():
    # 空轨迹（None 或 []）→ 不出 agent 段
    assert _build_agent_trace(agent_steps=None, agent_type="CHANGE_IMPACT") is None
    assert _build_agent_trace(agent_steps=[], agent_type="CHANGE_IMPACT") is None


def test_build_agent_trace_type_fallback():
    # agent_type 缺失（降级覆盖 meta 后 msg.agent_type 仍可能为 None）→ 回退 "AGENT"
    resp = _build_agent_trace(agent_steps=[{"tool": "t", "args": {}, "n": 0}], agent_type=None)
    assert resp is not None
    assert resp.type == "AGENT"


# ---- M41 legacy trace 追加 ----


async def test_stream_chat_persists_trace_dict(monkeypatch):
    """M41 legacy 路径：agent_steps 落 version:2 dict，含 request/retrieval/llm span。

    验证：(1) persist 时落部分 payload（仅 request+retrieval）；
    (2) 生成后同事务补写完整 payload（含 llm）；
    (3) llm span 正确挂在 request span 下（parent_id）。
    """
    import app.services.chat_service as cs

    async def fake_recall(session, query, top_k=8, **kw):
        return [], {"mode": "default", "merged": 0, "recall_ms": 5, "rerank_ms": 2}

    async def fake_stream_tokens(messages, *, usage_out=None, **kw):
        if usage_out is not None:
            usage_out.update({"prompt_tokens": 4, "completion_tokens": 2})
        yield "答"

    monkeypatch.setattr(cs.pipeline, "recall", fake_recall)
    async def _fake_enrich(_s, _r):
        return None
    monkeypatch.setattr(cs, "_enrich_content_types", _fake_enrich)
    import app.clients.llm_client as _llm_mod
    monkeypatch.setattr(_llm_mod.LLMClient, "configured",
                         property(lambda self: True))
    monkeypatch.setattr(cs.llm, "stream_tokens", fake_stream_tokens)
    monkeypatch.setattr(cs.settings, "conversation_history_turns", 0)

    _AddRecorder.flush_history = []  # 重置历史
    events: list[tuple[str, dict]] = []
    async for ev in cs.stream_chat(_AddRecorder(), "q"):
        events.append(ev)

    # ---- 不变量 1：persist 时落部分 payload（仅 request+retrieval，无 llm）----
    partial = _AddRecorder.flush_history[0]
    assert partial["version"] == 2
    partial_kinds = [s["kind"] for s in partial["spans"]]
    assert partial_kinds == ["request", "retrieval"]

    # ---- 不变量 2：最终 payload 含全部三种 kind ----
    payload = _AddRecorder.last_rlog_agent_steps
    assert payload is not None
    assert payload["version"] == 2
    kinds = [s["kind"] for s in payload["spans"]]
    assert kinds == ["request", "retrieval", "llm"]
    assert payload["summary"]["tokens"]["n_llm_calls"] == 1

    # ---- 不变量 3：llm span 的 parent_id == request span 的 span_id ----
    spans_by_kind = {s["kind"]: s for s in payload["spans"]}
    assert spans_by_kind["llm"]["parent_id"] == spans_by_kind["request"]["span_id"]


async def test_stream_chat_trace_no_llm_key(monkeypatch):
    """M41 no-LLM-key 变体：最终 payload 只含 request+retrieval（无 llm span）。"""
    import app.services.chat_service as cs

    async def fake_recall(session, query, top_k=8, **kw):
        return [], {"mode": "default", "merged": 0, "recall_ms": 5, "rerank_ms": 2,
                    "terms": ["q"]}

    monkeypatch.setattr(cs.pipeline, "recall", fake_recall)
    async def _fake_enrich(_s, _r):
        return None
    monkeypatch.setattr(cs, "_enrich_content_types", _fake_enrich)
    import app.clients.llm_client as _llm_mod
    monkeypatch.setattr(_llm_mod.LLMClient, "configured",
                         property(lambda self: False))
    monkeypatch.setattr(cs.settings, "conversation_history_turns", 0)

    _AddRecorder.flush_history = []
    events: list[tuple[str, dict]] = []
    async for ev in cs.stream_chat(_AddRecorder(), "q"):
        events.append(ev)

    payload = _AddRecorder.last_rlog_agent_steps
    assert payload is not None
    assert payload["version"] == 2
    kinds = [s["kind"] for s in payload["spans"]]
    assert kinds == ["request", "retrieval"]
    assert payload["summary"]["tokens"]["n_llm_calls"] == 0
