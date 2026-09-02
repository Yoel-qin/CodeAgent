"""Router（query_analysis）单测：真值表 / 规则兜底 / 无 key 降级 / json_mode 回归锁 / 成本挂账。"""
import asyncio

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.query_analysis import RouteDecision, decide_route, rule_classify

_JSON_CODE = '{"intent": "code", "confidence": 0.9, "simple_fact": false, "reason": "r"}'


class _FakeChatModel(BaseChatModel):
    """能触发 ``on_chat_model_start`` 的最小 fake：plain-class stub 不走回调管理器，
    成本账本（I-1）测试必须用真 ``BaseChatModel`` 才能验到 ``record_call``。"""

    text: str = _JSON_CODE

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.text))])

    @property
    def _llm_type(self) -> str:
        return "fake-route"

    def with_structured_output(self, schema, method=None, **kwargs):  # noqa: ARG002
        return self | PydanticOutputParser(pydantic_object=schema)


def test_decide_route_truth_table():
    assert decide_route(None) == "retrieve"
    assert decide_route(RouteDecision(intent="code", confidence=0.95, simple_fact=True)) == "retrieve"
    assert decide_route(RouteDecision(intent="code", confidence=0.5)) == "clarify"
    assert decide_route(RouteDecision(intent="code", confidence=0.85)) == "codenav"
    assert decide_route(RouteDecision(intent="doc", confidence=0.8)) == "docqa"
    assert decide_route(RouteDecision(intent="web", confidence=0.95)) == "retrieve"
    assert decide_route(RouteDecision(intent="other", confidence=0.85)) == "retrieve"


def test_rule_classify_keywords():
    assert rule_classify("DefaultMQProducerImpl 的 send 在哪个文件").intent == "code"
    assert rule_classify("刷盘机制文档怎么写").intent == "doc"


async def test_node_no_key_uses_rules(monkeypatch):
    from app.agent import query_analysis as qa
    monkeypatch.setattr(qa, "configured", lambda: False)
    state = await qa.query_analysis_node({"query": "CommitLog 在哪", "repo": "r",
                                          "conversation_id": "c", "history": []}, None)
    assert state["route"] == "codenav" and state["intent"] == "code"


async def test_node_llm_timeout_falls_back(monkeypatch):
    from app.agent import query_analysis as qa

    class SlowModel:
        def with_structured_output(self, _, method=None):
            async def _inv(_m, config=None):  # config：I-1 起分类调用挂成本回调 dict
                await asyncio.sleep(10)
            class R:
                ainvoke = staticmethod(_inv)
            return R()
    monkeypatch.setattr(qa, "configured", lambda: True)
    monkeypatch.setattr(qa, "chat_model_for", lambda _t="routing": SlowModel())
    state = await qa.query_analysis_node({"query": "x", "repo": "r",
                                          "conversation_id": "c", "history": []}, None)
    assert state["route"] in {"retrieve", "clarify", "codenav", "docqa"}


# ── 终审 I-1：Router 分类挂成本账本 ───────────────────────────────────────


async def test_node_llm_call_counts_into_cost_ledger(monkeypatch):
    """configurable["cost"] 传到 routing 档分类：LLM 路成功 → cost.llm_calls == 1。"""
    from app.agent import query_analysis as qa
    from app.agent.cost import CostController

    monkeypatch.setattr(qa, "configured", lambda: True)
    monkeypatch.setattr(qa, "chat_model_for", lambda _t="routing": _FakeChatModel())
    cost = CostController(max_tokens=1000, max_llm_calls=5)
    state = await qa.query_analysis_node(
        {"query": "CommitLog 在哪", "repo": "r", "conversation_id": "c", "history": []},
        {"configurable": {"cost": cost}})
    assert state["route"] == "codenav"  # stub 分类成功（非规则兜底路径）
    assert cost.llm_calls == 1


async def test_node_no_cost_config_still_classifies(monkeypatch):
    """无 cost（_cost_callbacks 回 {}）：分类照常，零行为变。"""
    from app.agent import query_analysis as qa

    monkeypatch.setattr(qa, "configured", lambda: True)
    monkeypatch.setattr(qa, "chat_model_for", lambda _t="routing": _FakeChatModel())
    state = await qa.query_analysis_node({"query": "CommitLog 在哪", "repo": "r",
                                          "conversation_id": "c", "history": []}, None)
    assert state["route"] == "codenav"


# ── 终审 I-2：json_mode 回归锁 ────────────────────────────────────────────


async def test_llm_classify_pins_json_mode(monkeypatch):
    """回归锁：with_structured_output 必须 method="json_mode"，且系统提示词含小写 json。

    两半缺一 DeepSeek 都 400：json_schema（thinking 档还禁 function_calling）一律
    ``400 This response_format type is unavailable now``；json_mode 又要求提示词
    含小写 ``json``（大小写敏感）。谁改回默认 method 或改写提示词，此测试即红。
    """
    from app.agent import query_analysis as qa

    captured: dict = {}

    class _Cap:
        def with_structured_output(self, _schema, method=None):
            captured["method"] = method

            async def _inv(_m, config=None):
                return RouteDecision(intent="code", confidence=0.9)

            class _R:
                ainvoke = staticmethod(_inv)
            return _R()

    monkeypatch.setattr(qa, "chat_model_for", lambda _t="routing": _Cap())
    d = await qa._llm_classify("DefaultMQProducer 在哪")
    assert d is not None and d.intent == "code"
    assert captured["method"] == "json_mode"
    assert "json" in qa._SYSTEM_PROMPT  # 区分大小写：必须是小写字面 json
