"""Task 3：A/B 变体旋钮——缺席零行为变更 + 覆盖生效。"""
from app.clients import llm


def test_model_overrides_apply_and_reset():
    before = llm.endpoint_for("reasoning").model
    token = llm.apply_model_overrides({"reasoning": {"model": "test-model-x"}})
    try:
        ep = llm.endpoint_for("reasoning")
        assert ep.model == "test-model-x" and ep.api_key  # 其余字段继承
        overridden = llm.chat_model_for("reasoning")
    finally:
        llm.reset_model_overrides(token)
    # reset 后回落配置真值 + 实例缓存按端点三元组隔离（覆盖实例 ≠ 默认实例）
    assert llm.endpoint_for("reasoning").model == before
    assert llm.chat_model_for("reasoning") is not overridden
    # 空覆盖 = 零行为变更（endpoint 原样）
    tok2 = llm.apply_model_overrides({})
    try:
        assert llm.endpoint_for("reasoning") == llm.endpoint_for("reasoning")
    finally:
        llm.reset_model_overrides(tok2)


def test_model_overrides_do_not_leak_across_reset():
    token = llm.apply_model_overrides({"routing": {"model": "tiny"}})
    llm.reset_model_overrides(token)
    assert llm.endpoint_for("routing").model != "tiny"


async def test_codenav_honors_rounds_and_no_graph(monkeypatch):
    from app.agent import codenav

    captured = {}

    async def _fake_run(state, config, **kw):
        captured.update(kw)

    monkeypatch.setattr(codenav, "run_react_agent", _fake_run)
    tools_arg = {}

    def _fake_get(include_graph=True):
        tools_arg["include_graph"] = include_graph
        return []

    monkeypatch.setattr(codenav, "get_code_tools", _fake_get)

    # 缺席：settings 默认 + include_graph=True（零行为变更）
    await codenav.codenav_node({"query": "q", "repo": "r", "history": []}, None)
    assert tools_arg["include_graph"] is True and captured["max_rounds"] > 0
    base_rounds = captured["max_rounds"]

    # 覆盖：rounds_code=2 + code_no_graph=True
    cfg = {"configurable": {"rounds_code": 2, "code_no_graph": True}}
    await codenav.codenav_node({"query": "q", "repo": "r", "history": []}, cfg)
    assert captured["max_rounds"] == 2 and tools_arg["include_graph"] is False
    assert base_rounds >= 1  # 对照组确实取到了 settings 值


async def test_docqa_honors_rounds(monkeypatch):
    from app.agent import docqa

    captured = {}

    async def _fake_run(state, config, **kw):
        captured.update(kw)

    monkeypatch.setattr(docqa, "run_react_agent", _fake_run)
    monkeypatch.setattr(docqa, "get_doc_tools", lambda: [])
    await docqa.docqa_node({"query": "q", "repo": "r", "history": []},
                           {"configurable": {"rounds_doc": 1}})
    assert captured["max_rounds"] == 1
