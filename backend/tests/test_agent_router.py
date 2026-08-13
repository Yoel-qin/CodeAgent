"""Orchestrator 路由 + 意图分类单测（无需 infra/网络）。"""
from __future__ import annotations

from app.agent.llm import _rule_intent, classify_intent
from app.agent.nodes import router as router_mod
from app.agent.nodes.query_analysis import query_analysis

# ---- route() 条件路由 ----


async def test_route_code_when_configured(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    assert router_mod.route({"intent": "code"}) == "code_understand"
    assert router_mod.route({"agent_type": "CODE_UNDERSTAND", "intent": "doc"}) == "code_understand"


async def test_route_fallback_when_not_configured(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: False)
    # 即使是 code 意图，无 key 也不进 Agent
    assert router_mod.route({"intent": "code", "agent_type": "CODE_UNDERSTAND"}) == "retrieve"


async def test_route_doc_when_configured(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    assert router_mod.route({"intent": "doc"}) == "doc_answer"
    # 显式 agent_type 优先于 intent
    assert router_mod.route({"agent_type": "DOC_ANSWER", "intent": "code"}) == "doc_answer"


async def test_route_non_agent_intents(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    # mixed/chitchat 无对应场景 Agent → retrieve 兜底（graph 现 → change_impact，见下）
    assert router_mod.route({"intent": "chitchat"}) == "retrieve"
    assert router_mod.route({"intent": "mixed"}) == "retrieve"


async def test_route_graph_to_change_impact(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    # graph 意图（依赖/结构/影响范围）→ 变更影响 Agent
    assert router_mod.route({"intent": "graph"}) == "change_impact"
    # 显式 agent_type 优先于 intent
    assert router_mod.route({"agent_type": "CHANGE_IMPACT", "intent": "code"}) == "change_impact"


async def test_route_bug_to_bug_diagnosis(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    # bug 意图（报错/异常/崩溃/为何失败）→ 缺陷诊断 Agent
    assert router_mod.route({"intent": "bug"}) == "bug_diagnosis"
    # 显式 agent_type 优先于 intent
    assert router_mod.route({"agent_type": "BUG_DIAGNOSIS", "intent": "code"}) == "bug_diagnosis"


async def test_route_review_to_code_review(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    # review 意图（代码审查/质量评估/改进建议/重构）→ 代码审查 Agent
    assert router_mod.route({"intent": "review"}) == "code_review"
    # 显式 agent_type 优先于 intent
    assert router_mod.route({"agent_type": "CODE_REVIEW", "intent": "code"}) == "code_review"


# ---- 规则意图兜底 ----


def test_rule_intent_code():
    assert _rule_intent("Account.getBalance 做了什么") == "code"
    assert _rule_intent("这个方法为什么这么写") == "code"


def test_rule_intent_doc():
    assert _rule_intent("怎么配置延迟消息等级") == "doc"
    assert _rule_intent("事务消息的使用说明") == "doc"


def test_rule_intent_default_code():
    assert _rule_intent("hello") == "code"


def test_rule_intent_bug():
    # 强 bug 信号（报错/崩溃/空指针）→ bug 意图（缺陷诊断）
    assert _rule_intent("为什么 checkLocalTransaction 会报错") == "bug"
    assert _rule_intent("这段代码为什么会崩溃") == "bug"
    assert _rule_intent("这里出现空指针异常 npe 怎么回事") == "bug"


def test_rule_intent_bug_not_misroute_doc():
    # 裸「异常」是弱信号——"事务消息异常的配置说明"是 doc 查询，不应误判为 bug
    assert _rule_intent("事务消息异常的配置说明") == "doc"


def test_rule_intent_review():
    # 代码审查强信号（审查/review/改进建议/重构）→ review 意图（代码审查）
    assert _rule_intent("帮我审查一下 getBalance 的实现") == "review"
    assert _rule_intent("review 一下这段代码，有什么改进建议") == "review"
    assert _rule_intent("这段代码需要重构吗") == "review"


def test_rule_intent_review_not_misroute_code():
    # 裸 code 查询（无审查词）→ code，不被 review 误吞
    assert _rule_intent("getBalance 做了什么") == "code"


async def test_route_test_to_test_generation(monkeypatch):
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    # test 意图（写/生成单元测试）→ 测试生成 Agent
    assert router_mod.route({"intent": "test"}) == "test_generation"
    # 显式 agent_type 优先于 intent
    assert router_mod.route({"agent_type": "TEST_GENERATION", "intent": "code"}) == "test_generation"


def test_rule_intent_test():
    # 测试生成强信号（写/生成测试/junit/unit test）→ test 意图（测试生成）
    assert _rule_intent("帮我给 withdraw 写单元测试") == "test"
    assert _rule_intent("generate unit tests for getBalance") == "test"
    assert _rule_intent("为这个方法写测试用例") == "test"


def test_rule_intent_test_not_misroute():
    # 裸 code 查询（无测试词）→ code，不被 test 误吞
    assert _rule_intent("getBalance 做了什么") == "code"
    # 去伪命中：hint 不收裸 test，故 "latest changes"（含 la**test**）不误判为 test
    assert _rule_intent("show me the latest changes") != "test"


async def test_classify_intent_no_key_uses_rule(monkeypatch):
    # 无 key → 走规则，绝不触网
    import app.agent.llm as llm_mod

    monkeypatch.setattr(llm_mod, "configured", lambda: False)
    assert await classify_intent("这段代码做了什么") == "code"
    assert await classify_intent("文档怎么写") == "doc"


async def test_classify_intent_llm_failure_falls_back(monkeypatch):
    import app.agent.llm as llm_mod

    monkeypatch.setattr(llm_mod, "configured", lambda: True)

    async def boom(*a, **k):
        raise RuntimeError("network down")

    class _FakeStructured:
        async def ainvoke(self, messages):
            return await boom()

    def fake_model():
        class M:
            def with_structured_output(self, schema):
                return _FakeStructured()
        return M()

    monkeypatch.setattr(llm_mod, "get_chat_model", fake_model)
    # LLM 失败 → 规则兜底（不抛）
    assert await classify_intent("方法调用链") == "code"


# ---- query_analysis 产出 intent ----


async def test_query_analysis_sets_intent(monkeypatch):
    from app.agent.llm import IntentSchema

    async def fake_rw(q):
        return {"semantic_query": q, "extra_keywords": ["Foo"]}

    async def fake_classify(q):
        return IntentSchema(intent="code", needs_collab=False)

    monkeypatch.setattr("app.agent.nodes.query_analysis.rewrite_query", fake_rw)
    monkeypatch.setattr("app.agent.nodes.query_analysis.classify_intent_and_collab", fake_classify)

    out = await query_analysis({"query": "A.m1 做了什么"})
    assert out["intent"] == "code"
    assert out["semantic_query"] == "A.m1 做了什么"
    assert "Foo" in out["keywords"]
    assert out["rewritten"] is True


# ---- web 意图（联网 MCP Agent）----


def test_rule_intent_web():
    # 联网强信号（联网/网上/search the web）→ web 意图（优先级最高，避免被 doc/code 吞）
    assert _rule_intent("帮我联网搜一下最新的库用法") == "web"
    assert _rule_intent("search the web for spring boot") == "web"


async def test_route_web_when_tools_present(monkeypatch):
    import app.agent.tools.web_tools as wt
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    monkeypatch.setattr(wt, "_web_tools", [object()])
    assert router_mod.route({"intent": "web"}) == "web_search"
    # 显式 agent_type 优先于 intent
    assert router_mod.route({"agent_type": "WEB_SEARCH", "intent": "code"}) == "web_search"


async def test_route_web_falls_back_when_no_tools(monkeypatch):
    import app.agent.tools.web_tools as wt
    monkeypatch.setattr(router_mod, "configured", lambda: True)
    monkeypatch.setattr(wt, "_web_tools", [])
    # MCP 未启用/不可达 → web 意图回落 KB retrieve，不留死路
    assert router_mod.route({"intent": "web"}) == "retrieve"
    assert router_mod.route({"agent_type": "WEB_SEARCH"}) == "retrieve"


async def test_route_web_not_configured(monkeypatch):
    # 无 LLM key → 即便有 web 工具也走 retrieve（Agent 需要 LLM）
    import app.agent.tools.web_tools as wt
    monkeypatch.setattr(router_mod, "configured", lambda: False)
    monkeypatch.setattr(wt, "_web_tools", [object()])
    assert router_mod.route({"intent": "web"}) == "retrieve"
