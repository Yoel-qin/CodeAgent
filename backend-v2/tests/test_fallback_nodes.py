"""Task 7：retrieve / clarify 兜底节点测试。

writer 桩 = 收集 list 的可调用（``_W``）；节点内 writer 统一 ``w = _safe_writer()``，
测试 monkeypatch ``nodes._safe_writer`` 注入收集器。core 函数（``hybrid_search`` /
``grep_code`` / ``get_doc_toc`` / ``read_doc_section``）与 LLM（``chat_model_for`` /
``configured``）全 monkeypatch——不打真 PG/Milvus/ES/LLM。
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


def _stub_doc_io(monkeypatch, toc_rows=None, contents=None):
    """钉死 doc 正文增强的 core IO（真 get_doc_toc/read_doc_section 会连 PG）。

    toc_rows：get_doc_toc 返回的 toc 列表（None → 空 toc，增强空转）；
    contents：{anchor: 正文}——anchor 未登记 → read 返回 error 形（未命中跳过）。
    """
    monkeypatch.setattr(nodes, "get_doc_toc", lambda *a: {"toc": toc_rows or []})
    known = contents or {}

    def _read(*a):
        anchor = a[2]
        if anchor in known:
            return {"content": known[anchor]}
        return {"error": "section not found"}
    monkeypatch.setattr(nodes, "read_doc_section", _read)


# ── brief 逐字测试 ─────────────────────────────────────────────────────────


async def test_retrieve_no_key_emits_snippets(monkeypatch):
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    monkeypatch.setattr(nodes, "hybrid_search",
                        lambda *a: {"results": [{"section_id": "s1", "doc_name": "a.md",
                                                 "title": "T", "anchor": "x", "module": None,
                                                 "score": 0.9}], "recall": 1})
    monkeypatch.setattr(nodes, "grep_code", lambda *a: _grep_result())
    _stub_doc_io(monkeypatch)  # 空 toc：增强空转，本测试钉的是无 key 片段分支

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
    _stub_doc_io(monkeypatch)
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
    _stub_doc_io(monkeypatch,
                 toc_rows=[{"document_id": 7, "doc_name": "a.md", "anchor": "x"}],
                 contents={"x": "PG 正文内容"})
    monkeypatch.setattr(nodes, "configured", lambda: True)
    seen = {}

    class _M:
        async def astream(self, messages, config=None):
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
    assert "a.md#T" in user and "PG 正文内容" in user  # doc 段落含 read_doc_section 补的正文


async def test_retrieve_core_paths_fail_still_completes(monkeypatch):
    """两路 core 全挂：独立降级不抛，仍发 retrieval + 片段头 token。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)

    def _boom(*_a):
        raise RuntimeError("pg down")
    monkeypatch.setattr(nodes, "hybrid_search", _boom)
    monkeypatch.setattr(nodes, "grep_code", _boom)
    _stub_doc_io(monkeypatch)  # doc 结果为空 → 增强空转（防御真 IO）
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
    _stub_doc_io(monkeypatch)  # doc 结果为空 → 增强空转（防御真 IO）
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
        def invoke(self, messages, config=None):
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


# ── doc 正文增强（R1：F-1 修复） ──────────────────────────────────────────


def _doc_only_state():
    return {"query": "刷盘机制", "repo": "mini", "conversation_id": "c", "history": [],
            "intent": "doc", "confidence": 0.9, "route": "retrieve"}


def _doc_hit(**extra):
    """hybrid 冻结形状的 doc 命中（无 content 字段），extra 覆盖。"""
    return {"section_id": "s1", "doc_name": "a.md", "title": "T", "anchor": "x",
            "module": None, "score": 0.9} | extra


# ── doc 正文增强（R1：F-1 修复） ──────────────────────────────────────────


async def test_retrieve_doc_body_enriched_into_snippet(monkeypatch):
    """doc 命中经 TOC 映射 read_doc_section 补正文：无 key 片段含正文 + 位置参数 (repo, id, anchor)。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    monkeypatch.setattr(nodes, "hybrid_search",
                        lambda *a: {"results": [_doc_hit()], "recall": 1})
    monkeypatch.setattr(nodes, "grep_code", lambda *a: _grep_result(matches=[]))
    monkeypatch.setattr(nodes, "configured", lambda: False)
    seen = {}
    _stub_doc_io(monkeypatch,
                 toc_rows=[{"document_id": 7, "doc_name": "a.md", "anchor": "x"}],
                 contents={"x": "同步刷盘的正文段落"})
    real_read = nodes.read_doc_section

    def _read(*a):
        seen["read_args"] = a
        return real_read(*a)
    monkeypatch.setattr(nodes, "read_doc_section", _read)
    await nodes.retrieve_node(_doc_only_state(), {"configurable": {}})
    assert seen["read_args"] == ("mini", 7, "x")  # to_thread 位置参数铁律
    text = "".join(c["data"]["content"] for c in w if c["event"] == "token")
    assert "[a.md#T]" in text and "同步刷盘的正文段落" in text


async def test_retrieve_doc_body_enriched_into_llm_context(monkeypatch):
    """LLM 路：context 的 doc 段 = 标题行 + 正文前 500 字；PG 正文覆盖 hybrid 自带 content。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    monkeypatch.setattr(nodes, "hybrid_search",
                        lambda *a: {"results": [_doc_hit(content="hybrid 自带正文")], "recall": 1})
    monkeypatch.setattr(nodes, "grep_code", lambda *a: _grep_result(matches=[]))
    monkeypatch.setattr(nodes, "configured", lambda: True)
    seen = {}
    body = "PG 正文" * 120  # 600 字 > 500 → 增强层截前 500 字
    _stub_doc_io(monkeypatch,
                 toc_rows=[{"document_id": 7, "doc_name": "a.md", "anchor": "x"}],
                 contents={"x": body})

    class _M:
        async def astream(self, messages, config=None):
            seen["messages"] = list(messages)
            yield SimpleNamespace(content="ok")

    monkeypatch.setattr(nodes, "chat_model_for", lambda _t="reasoning": _M())
    await nodes.retrieve_node(_doc_only_state(), {"configurable": {}})
    user = seen["messages"][-1].content
    assert "[a.md#T]" in user
    assert body[:500] in user and body not in user  # PG 正文截前 500 字（覆盖 hybrid 自带）
    assert "hybrid 自带正文" not in user


async def test_retrieve_toc_failure_keeps_title_line(monkeypatch):
    """TOC 挂：正文增强跳过、退回标题行，不触发整体降级 token。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    monkeypatch.setattr(nodes, "hybrid_search",
                        lambda *a: {"results": [_doc_hit()], "recall": 1})
    monkeypatch.setattr(nodes, "grep_code", lambda *a: _grep_result(matches=[]))
    monkeypatch.setattr(nodes, "configured", lambda: False)

    def _toc_boom(*_a):
        raise RuntimeError("pg down")

    def _read_boom(*_a):
        raise AssertionError("TOC 挂后不得逐条 read")
    monkeypatch.setattr(nodes, "get_doc_toc", _toc_boom)
    monkeypatch.setattr(nodes, "read_doc_section", _read_boom)
    await nodes.retrieve_node(_doc_only_state(), {"configurable": {}})
    names = [c["event"] for c in w]
    assert names[0] == "retrieval" and names[-1] == "token"
    text = w[-1]["data"]["content"]
    assert "[a.md#T]" in text and "检索降级失败" not in text  # 降级链未被增强破坏


async def test_retrieve_read_failure_or_miss_skips_row(monkeypatch):
    """单条 read 抛异常 / 返回 error 形：跳过该条，标题行保留，其余照常。"""
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    monkeypatch.setattr(nodes, "hybrid_search",
                        lambda *a: {"results": [_doc_hit(), _doc_hit(anchor="y", title="T2")],
                                   "recall": 2})
    monkeypatch.setattr(nodes, "grep_code", lambda *a: _grep_result(matches=[]))
    monkeypatch.setattr(nodes, "configured", lambda: False)

    def _toc(*_a):
        return {"toc": [{"document_id": 7, "doc_name": "a.md", "anchor": "x"},
                        {"document_id": 7, "doc_name": "a.md", "anchor": "y"}]}

    def _read(*a):
        if a[2] == "x":
            raise RuntimeError("io down")
        return {"error": "section not found"}
    monkeypatch.setattr(nodes, "get_doc_toc", _toc)
    monkeypatch.setattr(nodes, "read_doc_section", _read)
    await nodes.retrieve_node(_doc_only_state(), {"configurable": {}})
    text = w[-1]["data"]["content"]
    assert "[a.md#T]" in text and "[a.md#T2]" in text  # 两条命中都保留标题行
    assert "检索降级失败" not in text


# ── cost 挂账（Task 10 评审遗留 ①） ───────────────────────────────────────


async def test_retrieve_cost_callback_records_llm_call(monkeypatch):
    """configurable 里的 cost 经 CostCallbackHandler 挂账：retrieve 的 LLM 调用计 1 次。"""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from app.agent.cost import CostController
    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    monkeypatch.setattr(nodes, "hybrid_search", lambda *a: {"results": [], "recall": 0})
    monkeypatch.setattr(nodes, "grep_code", lambda *a: _grep_result(matches=[]))
    _stub_doc_io(monkeypatch)
    monkeypatch.setattr(nodes, "configured", lambda: True)
    monkeypatch.setattr(nodes, "chat_model_for",
                        lambda _t="reasoning": GenericFakeChatModel(
                            messages=iter([AIMessage(content="答案")])))
    cost = CostController(max_tokens=1000, max_llm_calls=5)
    state = {"query": "putMessage 在哪", "repo": "mini", "conversation_id": "c",
             "history": [], "intent": "code", "confidence": 0.9, "route": "retrieve"}
    await nodes.retrieve_node(state, {"configurable": {"cost": cost}})
    assert cost.llm_calls == 1


async def test_clarify_cost_callback_records_llm_call(monkeypatch):
    """clarify 的 extraction 档调用同样挂账（I-1 同轮 nit：retrieve 有断言、clarify 漏）。"""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from app.agent.cost import CostController

    class _FakeChatModel(BaseChatModel):
        """plain-class stub 不走回调管理器，须真 BaseChatModel 才能验到 record_call。"""

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="请补充类名"))])

        @property
        def _llm_type(self) -> str:
            return "fake-clarify"

    w = _W()
    monkeypatch.setattr(nodes, "_safe_writer", lambda: w)
    monkeypatch.setattr(nodes, "configured", lambda: True)
    monkeypatch.setattr(nodes, "chat_model_for", lambda _t="extraction": _FakeChatModel())
    cost = CostController(max_tokens=1000, max_llm_calls=5)
    await nodes.clarify_node({"query": "模糊问题", "repo": "r", "conversation_id": "c",
                              "history": [], "intent": "other", "confidence": 0.3,
                              "route": "clarify"}, {"configurable": {"cost": cost}})
    assert cost.llm_calls == 1
    assert "请补充类名" in "".join(
        c["data"]["content"] for c in w if c["event"] == "token")


async def test_retrieve_respects_configurable_top_k(monkeypatch):
    """M8 变体旋钮接活：configurable["top_k"] 穿透到 hybrid_search；缺席 = 既有常量默认。"""
    from app.agent import nodes

    monkeypatch.setattr(nodes, "configured", lambda: False)
    seen = {}

    def _fake_hybrid(repo, query, top_k, module):
        seen["top_k"] = top_k
        return {"results": []}

    monkeypatch.setattr(nodes, "hybrid_search", _fake_hybrid)
    monkeypatch.setattr(nodes, "grep_code",
                        lambda *a: {"matches": [], "total_count": 0, "truncated": False,
                                    "engine": "python"})
    await nodes.retrieve_node({"query": "刷盘机制怎么写的", "repo": "mini", "history": []},
                              {"configurable": {"top_k": 3}})
    assert seen["top_k"] == 3
    await nodes.retrieve_node({"query": "刷盘机制怎么写的", "repo": "mini", "history": []}, None)
    assert seen["top_k"] == 8  # 缺席 = 常量默认（零行为变更）
