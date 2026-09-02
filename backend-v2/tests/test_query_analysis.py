from app.agent.query_analysis import RouteDecision, decide_route, rule_classify


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
    import asyncio

    from app.agent import query_analysis as qa

    class SlowModel:
        def with_structured_output(self, _):
            async def _inv(_m):
                await asyncio.sleep(10)
            class R:
                ainvoke = staticmethod(_inv)
            return R()
    monkeypatch.setattr(qa, "configured", lambda: True)
    monkeypatch.setattr(qa, "chat_model_for", lambda _t="routing": SlowModel())
    state = await qa.query_analysis_node({"query": "x", "repo": "r",
                                          "conversation_id": "c", "history": []}, None)
    assert state["route"] in {"retrieve", "clarify", "codenav", "docqa"}
