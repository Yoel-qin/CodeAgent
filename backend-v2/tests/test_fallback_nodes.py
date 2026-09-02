"""Task 7：retrieve / clarify 兜底节点测试。

writer 桩 = 收集 list 的可调用（``_W``）；节点内 writer 统一 ``w = _safe_writer()``，
测试 monkeypatch ``nodes._safe_writer`` 注入收集器。core 函数（``hybrid_search`` /
``grep_code``）与 LLM（``chat_model_for`` / ``configured``）全 monkeypatch——
不打真 PG/Milvus/ES/LLM。
"""
from types import SimpleNamespace

from app.agent import nodes


class _W(list):
    def __call__(self, chunk):
        self.append(chunk)


def _grep_result(**overrides):
    base = {"matches": [{"file": "A.java", "line": 3, "content": "int x"}],
            "total_count": 1, "truncated": False, "engine": "python"}
    base.update(overrides)
    return base


# ── brief 逐字测试 ─────────────────────────────────────────────────────────


async def test_retrieve_no_key_emits_snippets(monkeypatch):
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    monkeypatch.setattr(nodes, "hybrid_search",
                        lambda *a: {"results": [{"section_id": "s1", "doc_name": "a.md",
                                                 "title": "T", "anchor": "x", "module": None,
                                                 "score": 0.9}], "recall": 1})
    monkeypatch.setattr(nodes, "grep_code", lambda *a: _grep_result())

    def _boom(_t="reasoning"):
        raise AssertionError("无 key 时不得触碰 LLM")
    monkeypatch.setattr(nodes, "chat_model_for", _boom)
    # 钉死无 key 分支：本机根 .env 有真 key，real configured() 会翻 True 误入 LLM 路
    monkeypatch.setattr(nodes, "configured", lambda: False)
    state = {"query": "putMessage 在哪", "repo": "mini", "conversation_id": "c",
             "history": [], "intent": "code", "confidence": 0.9, "route": "retrieve"}
    await nodes.retrieve_node(state, {"configurable": {}})
    names = [c["event"] for c in w]
    assert names[0] == "retrieval" and "citation" in names and names[-1] == "token"
    text = "".join(c["data"]["content"] for c in w if c["event"] == "token")
    assert "未配置 LLM" in text and "A.java:3" in text
    cites = [c["data"] for c in w if c["event"] == "citation"]
    assert any(c.get("kind") == "code" and c["file_path"] == "A.java" for c in cites)
    assert any(c.get("kind") == "doc" for c in cites)


async def test_clarify_template_fallback(monkeypatch):
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)

    class Boom:
        def invoke(self, *_a, **_k):
            raise RuntimeError("no key")
    monkeypatch.setattr(nodes, "chat_model_for", lambda _t="extraction": Boom())
    await nodes.clarify_node({"query": "q", "repo": "r", "conversation_id": "c",
                              "history": [], "intent": "other", "confidence": 0.3,
                              "route": "clarify"}, {"configurable": {}})
    toks = [c["data"]["content"] for c in w if c["event"] == "token"]
    assert "类名" in "".join(toks)


# ── 补充：事件契约细节 ─────────────────────────────────────────────────────


async def test_retrieve_event_payload_shape(monkeypatch):
    """retrieval 事件载荷冻结形状：mode/intent/confidence/code_hits/doc_hits。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    monkeypatch.setattr(nodes, "hybrid_search",
                        lambda *a: {"results": [{"doc_name": "a.md", "title": "T", "anchor": "x"}],
                                   "recall": 1})
    monkeypatch.setattr(nodes, "grep_code",
                        lambda *a: _grep_result(matches=[{"file": "A.java", "line": 3, "content": "x"},
                                                         {"file": "B.java", "line": 7, "content": "y"}],
                                                total_count=2))
    monkeypatch.setattr(nodes, "configured", lambda: False)
    await nodes.retrieve_node({"query": "putMessage 在哪", "repo": "mini",
                               "conversation_id": "c", "history": [],
                               "intent": "code", "confidence": 0.9, "route": "retrieve"},
                              {"configurable": {}})
    ev = w[0]
    assert ev["event"] == "retrieval"
    assert ev["data"] == {"mode": "retrieve", "intent": "code", "confidence": 0.9,
                          "code_hits": 2, "doc_hits": 1}
    cites = [c["data"] for c in w if c["event"] == "citation"]
    assert cites[0] == {"kind": "doc", "doc_id": "a.md", "section": "x", "label": "a.md#T"}
    assert cites[1] == {"kind": "code", "file_path": "A.java", "start_line": 3,
                        "end_line": 3, "label": "A.java:3"}


async def test_retrieve_llm_streams_tokens_in_order(monkeypatch):
    """LLM 可用：context 拼进 user 消息 + astream 逐 chunk 发 token（顺序保持）。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    monkeypatch.setattr(nodes, "hybrid_search",
                        lambda *a: {"results": [{"doc_name": "a.md", "title": "T", "anchor": "x",
                                                 "content": "文档正文"}], "recall": 1})
    monkeypatch.setattr(nodes, "grep_code", lambda *a: _grep_result())
    monkeypatch.setattr(nodes, "configured", lambda: True)
    seen = {}

    class _M:
        async def astream(self, messages):
            seen["messages"] = list(messages)
            for c in ("第一段", "第二段"):
                yield SimpleNamespace(content=c)

    monkeypatch.setattr(nodes, "chat_model_for", lambda _t="reasoning": _M())
    state = {"query": "putMessage 在哪", "repo": "mini", "conversation_id": "c",
             "history": [{"role": "user", "content": "上一问"}, {"role": "assistant", "content": "上一答"}],
             "intent": "code", "confidence": 0.9, "route": "retrieve"}
    await nodes.retrieve_node(state, {"configurable": {}})
    toks = [c["data"]["content"] for c in w if c["event"] == "token"]
    assert toks == ["第一段", "第二段"]
    msgs = seen["messages"]
    assert msgs[0].__class__.__name__ == "SystemMessage" and "只依据给定材料回答" in msgs[0].content
    joined = "".join(getattr(m, "content", "") for m in msgs)
    assert "上一问" in joined and "上一答" in joined  # history 注入
    user = msgs[-1].content
    assert "putMessage 在哪" in user and "A.java:3" in user and "int x" in user  # query + context
    assert "a.md#T" in user and "文档正文" in user


async def test_retrieve_core_paths_fail_still_completes(monkeypatch):
    """两路 core 全挂：独立降级不抛，仍发 retrieval + 片段头 token。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)

    def _boom(*_a):
        raise RuntimeError("pg down")
    monkeypatch.setattr(nodes, "hybrid_search", _boom)
    monkeypatch.setattr(nodes, "grep_code", _boom)
    monkeypatch.setattr(nodes, "configured", lambda: False)
    state = {"query": "x", "repo": "r", "conversation_id": "c", "history": [],
             "intent": "other", "confidence": 0.4, "route": "retrieve"}
    out = await nodes.retrieve_node(state, {"configurable": {}})  # 不抛
    assert out == {"answer": None}
    names = [c["event"] for c in w]
    assert names[0] == "retrieval" and names[-1] == "token"
    assert w[0]["data"]["code_hits"] == 0 and w[0]["data"]["doc_hits"] == 0
    assert "未配置 LLM" in w[-1]["data"]["content"]


async def test_retrieve_outer_guard_emits_failure_token(monkeypatch):
    """整体兜底：任何逃逸异常 → token 事件 [检索降级失败: 类型名]，永不炸。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)

    def _boom():
        raise ValueError("config broken")
    monkeypatch.setattr(nodes, "hybrid_search", lambda *a: {"results": [], "recall": 0})
    monkeypatch.setattr(nodes, "grep_code", lambda *a: _grep_result(matches=[]))
    monkeypatch.setattr(nodes, "configured", _boom)
    state = {"query": "x", "repo": "r", "conversation_id": "c", "history": [],
             "intent": "other", "confidence": 0.4, "route": "retrieve"}
    out = await nodes.retrieve_node(state, {"configurable": {}})  # 不抛
    assert out == {"answer": None}
    names = [c["event"] for c in w]
    assert names[0] == "retrieval" and names[-1] == "token"
    assert "检索降级失败" in w[-1]["data"]["content"] and "ValueError" in w[-1]["data"]["content"]


# ── clarify 补充 ───────────────────────────────────────────────────────────


async def test_clarify_llm_success_streams_question(monkeypatch):
    """extraction 档成功：追问文本按 64 字符切片发 token + retrieval(mode=clarify) 先行。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    question = "您" + "想" * 70 + "问哪个类？"  # 76 字 → 2 个切片
    monkeypatch.setattr(nodes, "configured", lambda: True)

    class _M:
        def invoke(self, messages):
            assert messages[0].__class__.__name__ == "SystemMessage"
            assert messages[-1].content == "模糊问题"
            return SimpleNamespace(content=question)

    monkeypatch.setattr(nodes, "chat_model_for", lambda _t="extraction": _M())
    await nodes.clarify_node({"query": "模糊问题", "repo": "r", "conversation_id": "c",
                              "history": [], "intent": "other", "confidence": 0.3,
                              "route": "clarify"}, {"configurable": {}})
    assert w[0]["event"] == "retrieval" and w[0]["data"] == {
        "mode": "clarify", "intent": "other", "confidence": 0.3, "code_hits": 0, "doc_hits": 0}
    toks = [c["data"]["content"] for c in w if c["event"] == "token"]
    assert len(toks) == 2 and all(len(t) <= 64 for t in toks)
    assert "".join(toks) == question


async def test_clarify_no_key_uses_template_without_llm(monkeypatch):
    """无 key：不触碰 LLM，直接模板。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)

    def _boom(_t="extraction"):
        raise AssertionError("无 key 时不得触碰 LLM")
    monkeypatch.setattr(nodes, "configured", lambda: False)
    monkeypatch.setattr(nodes, "chat_model_for", _boom)
    await nodes.clarify_node({"query": "q", "repo": "r", "conversation_id": "c",
                              "history": [], "intent": "other", "confidence": 0.3,
                              "route": "clarify"}, {"configurable": {}})
    toks = [c["data"]["content"] for c in w if c["event"] == "token"]
    assert "类名" in "".join(toks)
