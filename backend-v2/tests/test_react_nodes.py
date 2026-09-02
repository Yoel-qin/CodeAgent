"""Task 8：ReAct 骨架 + CodeNav 节点测试（brief 逐字，两处 langchain-core 1.6.1 适配）。

适配（详见 task-8-report）：
1. ``StructuredTool(name=..., coroutine=...)`` 在 1.6.1 必填 ``args_schema``（不传 →
   ValidationError）→ 沿 Task 5 ``test_tools_loader`` 同款透传 JSON-schema dict
   （``_parse_input`` 对 dict schema 不校验、kwargs 原样透传——若改用 from_function 按
   ``**kw`` 签名推断，会把 model args 换成 ``{"kw": None}``，wrap 内二次 ainvoke 即炸）。
2. ``GenericFakeChatModel`` 不实现 ``bind_tools``（基类直接 NotImplementedError，ReAct
   构造即炸）→ ``_FakeToolModel`` 子类补一个返回自身的 ``bind_tools``（脚本化行为与
   GenericFakeChatModel 完全一致，即 brief 注记 StubChatModel 退路的轻量版）。

循环检测路径由 Task 5 的 wrap 单测覆盖，不在此重复。
"""
import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from app.agent import codenav, react_base

_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}


def _tool(name: str, result: dict) -> StructuredTool:
    async def _fn(**kw):
        return json.dumps(result, ensure_ascii=False)
    return StructuredTool(name=name, description="t", args_schema=_EMPTY_SCHEMA, coroutine=_fn)


class _FakeToolModel(GenericFakeChatModel):
    """GenericFakeChatModel + bind_tools 返自身（fake 忽略绑定参数，ReAct 可构造）。"""

    def bind_tools(self, tools, **kwargs):
        return self


GREP_RESULT = {"matches": [{"file": "a/CommitLog.java", "line": 10, "content": "putMessage"}],
               "total_count": 1, "truncated": False, "engine": "python"}


class _W(list):
    def __call__(self, chunk):
        self.append(chunk)


async def test_react_flow_step_citation_token(monkeypatch):
    model = _FakeToolModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "grep_code", "args": {"pattern": "putMessage"},
                                           "id": "c1"}]),
        AIMessage(content="位于 a/CommitLog.java:10"),
    ]))
    monkeypatch.setattr(react_base, "chat_model_for", lambda _t="reasoning": model)
    monkeypatch.setattr(react_base, "configured", lambda: True)
    monkeypatch.setattr(codenav, "get_code_tools",
                        lambda: [_tool("grep_code", GREP_RESULT)])
    w = _W()
    monkeypatch.setattr(react_base, "_safe_writer", lambda: w)
    state = {"query": "putMessage 在哪", "repo": "mini", "conversation_id": "c",
             "history": [], "intent": "code", "confidence": 0.9, "route": "codenav"}
    await codenav.codenav_node(state, {"configurable": {}})
    kinds = [(c["event"], c["data"].get("tool")) for c in w]
    assert ("retrieval", None) in kinds
    assert ("agent_step", "grep_code") in kinds
    cites = [c["data"] for c in w if c["event"] == "citation"]
    assert cites and cites[0]["file_path"] == "a/CommitLog.java" and cites[0]["start_line"] == 10
    assert any(c["event"] == "token" for c in w)


async def test_react_no_key_degrades_to_retrieve(monkeypatch):
    called = {}
    monkeypatch.setattr(react_base, "configured", lambda: False)
    async def _fake_retrieve(state, config):
        called["yes"] = True
    monkeypatch.setattr(react_base, "retrieve_node", _fake_retrieve)
    monkeypatch.setattr(codenav, "get_code_tools", lambda: [])
    await codenav.codenav_node({"query": "q", "repo": "r", "conversation_id": "c",
                                "history": [], "intent": "code", "confidence": 0.9,
                                "route": "codenav"}, {"configurable": {}})
    assert called.get("yes") is True


async def test_react_no_key_with_tools_degrades_without_react(monkeypatch):
    """分支③（Task 8 评审遗留补测）：工具非空但无 key —— 不进 ReAct，直接 retrieve 兜底。

    现有 ``test_react_no_key_degrades_to_retrieve`` stub 的是空工具列表，实际走的是
    「工具服务不可用」分支②；本测试钉住「无 key 短路在 ReAct 构造之前」：create_react_agent
    一旦被触达即炸。
    """
    called = {}
    monkeypatch.setattr(react_base, "configured", lambda: False)

    async def _fake_retrieve(state, config):
        called["yes"] = True
    monkeypatch.setattr(react_base, "retrieve_node", _fake_retrieve)

    def _boom(**_kw):
        raise AssertionError("无 key 时不得构造 ReAct agent")
    monkeypatch.setattr(react_base, "create_react_agent", _boom)
    monkeypatch.setattr(codenav, "get_code_tools", lambda: [_tool("grep_code", GREP_RESULT)])
    await codenav.codenav_node({"query": "q", "repo": "r", "conversation_id": "c",
                                "history": [], "intent": "code", "confidence": 0.9,
                                "route": "codenav"}, {"configurable": {}})
    assert called.get("yes") is True


async def test_react_recursion_overflow_degrades(monkeypatch):
    """fake 模型永远发 tool_calls → recursion_limit 触发 GraphRecursionError → retrieve 降级。"""
    model = _FakeToolModel(messages=iter(
        AIMessage(content="", tool_calls=[{"name": "grep_code", "args": {"pattern": "x"},
                                           "id": f"c{i}"}]) for i in range(100)))
    monkeypatch.setattr(react_base, "chat_model_for", lambda _t="reasoning": model)
    monkeypatch.setattr(react_base, "configured", lambda: True)
    monkeypatch.setattr(codenav, "get_code_tools", lambda: [_tool("grep_code", GREP_RESULT)])
    degraded = {}
    async def _fake_retrieve(state, config):
        degraded["yes"] = True
    monkeypatch.setattr(react_base, "retrieve_node", _fake_retrieve)
    await codenav.codenav_node({"query": "q", "repo": "r", "conversation_id": "c",
                                "history": [], "intent": "code", "confidence": 0.9,
                                "route": "codenav"}, {"configurable": {}})
    assert degraded.get("yes") is True


# ── docqa 无引用拒答 v1（收尾检查：ReAct 跑完 + tracker.citations 空 → 提示 token） ──


def _state(route: str) -> dict:
    return {"query": "q", "repo": "r", "conversation_id": "c", "history": [],
            "intent": "doc", "confidence": 0.9, "route": route}


async def test_docqa_react_without_citations_appends_notice(monkeypatch):
    """模型不调工具就作答（tracker 无 citation）→ 追加「未找到可引用的文档依据」token。"""
    from app.agent import docqa
    w = _W()
    monkeypatch.setattr(docqa, "_safe_writer", lambda: w)
    monkeypatch.setattr(react_base, "_safe_writer", lambda: w)
    monkeypatch.setattr(react_base, "chat_model_for",
                        lambda _t="reasoning": _FakeToolModel(messages=iter(
                            [AIMessage(content="文档里说 foo")])))
    monkeypatch.setattr(react_base, "configured", lambda: True)
    monkeypatch.setattr(docqa, "get_doc_tools",
                        lambda: [_tool("doc_hybrid_search", {"results": []})])
    await docqa.docqa_node(_state("docqa"), {"configurable": {}})
    tokens = [c["data"]["content"] for c in w if c["event"] == "token"]
    assert tokens and tokens[-1].startswith("\n\n[未找到可引用的文档依据")


async def test_docqa_degraded_path_appends_no_notice(monkeypatch):
    """降级路径（工具挂/无 key → retrieve 接管）不加提示——retrieve 自产自己的 citation。"""
    from app.agent import docqa
    w = _W()
    monkeypatch.setattr(docqa, "_safe_writer", lambda: w)
    monkeypatch.setattr(react_base, "_safe_writer", lambda: w)
    monkeypatch.setattr(react_base, "configured", lambda: False)

    async def _fake_retrieve(state, config):
        w.append({"event": "token", "data": {"content": "检索片段"}})
    monkeypatch.setattr(react_base, "retrieve_node", _fake_retrieve)
    monkeypatch.setattr(docqa, "get_doc_tools", lambda: [])
    await docqa.docqa_node(_state("docqa"), {"configurable": {}})
    assert all("未找到可引用" not in c["data"]["content"]
               for c in w if c["event"] == "token")
