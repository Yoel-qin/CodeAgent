"""M42 CostController 单元测试（纯函数，零外部依赖）。"""
import pytest

from app.agent.cost import BudgetExceeded, CostController, make_cost_controller


def _ctl(**kw):
    return CostController(max_tokens=kw.get("max_tokens", 100),
                          max_llm_calls=kw.get("max_llm_calls", 3))


def test_token_gate_boundary():
    c = _ctl(max_tokens=100)
    c.record_usage(prompt=60, completion=40)   # =cap 不超
    assert c.exceeded is None
    c.record_usage(prompt=1)                    # +1 超
    assert isinstance(c.exceeded, BudgetExceeded)
    assert c.exceeded.reason == "tokens"
    assert (c.exceeded.spent, c.exceeded.cap) == (101, 100)


def test_call_gate_boundary():
    c = _ctl(max_llm_calls=2)
    c.record_call()
    c.record_call()                             # =cap 不超
    assert c.exceeded is None
    c.record_call()                             # 第 3 次超
    assert c.exceeded.reason == "llm_calls"


def test_check_raises_only_after_exceeded():
    c = _ctl()
    c.check()                                   # 未超：不抛
    c.record_usage(prompt=999)
    with pytest.raises(BudgetExceeded) as ei:
        c.check()
    assert ei.value.spent == 999 and ei.value.cap == 100


def test_record_marks_never_raises():
    """mark 侧绝不抛（langchain 吞回调异常，抛了也白抛且可能打断宿主）。"""
    c = _ctl(max_llm_calls=1)
    c.record_call()
    c.record_call()                             # 只标记
    assert c.exceeded is not None               # 到这里没抛即通过


def test_estimated_flag_sticky():
    c = _ctl()
    c.record_usage(prompt=10)
    assert c.estimated is False
    c.record_usage(completion=4, estimated=True)
    assert c.estimated is True
    c.record_usage(prompt=1)
    assert c.estimated is True                  # 传染后不回落


def test_to_meta_shape():
    c = _ctl(max_tokens=50, max_llm_calls=2)
    c.record_call()
    c.record_usage(prompt=30, completion=10)
    assert c.to_meta() == {
        "enabled": True, "spent_tokens": 40, "estimated": False,
        "llm_calls": 1, "cap_tokens": 50, "cap_llm_calls": 2, "exceeded": None,
    }


def test_to_meta_with_exceeded():
    c = _ctl(max_tokens=5)
    c.record_usage(prompt=9)
    assert c.to_meta()["exceeded"] == "tokens"


def test_notice_text():
    c = _ctl(max_tokens=5)
    c.record_usage(prompt=9)
    assert "预算" in c.exceeded.notice()


def test_factory_off_by_default(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "cost_control_enabled", False)
    assert make_cost_controller() is None
    monkeypatch.setattr(settings, "cost_control_enabled", True)
    monkeypatch.setattr(settings, "cost_max_tokens_per_request", 123)
    monkeypatch.setattr(settings, "cost_max_llm_calls", 4)
    c = make_cost_controller()
    assert isinstance(c, CostController)
    assert (c.max_tokens, c.max_llm_calls) == (123, 4)


# ---- CostCallbackHandler 记量（Task 3）----

class _Msg:
    def __init__(self, um=None):
        self.usage_metadata = um


class _Gen:
    def __init__(self, msg=None, text=""):
        self.message = msg
        self.text = text


class _Resp:
    def __init__(self, gens, llm_output=None):
        self.generations = [gens]
        self.llm_output = llm_output


def test_cost_handler_records_true_usage():
    from app.agent.llm import CostCallbackHandler
    c = _ctl(max_tokens=1000, max_llm_calls=5)
    h = CostCallbackHandler(c)
    h.on_llm_start({}, ["p"], run_id="r1")
    h.on_llm_end(_Resp([_Gen(_Msg({"input_tokens": 10, "output_tokens": 5}))]), run_id="r1")
    assert (c.llm_calls, c.spent_tokens, c.estimated) == (1, 15, False)


def test_cost_handler_estimates_without_usage():
    from app.agent.llm import CostCallbackHandler
    c = _ctl(max_tokens=1000, max_llm_calls=5)
    h = CostCallbackHandler(c)
    h.on_llm_start({}, ["p"], run_id="r1")
    h.on_llm_end(_Resp([_Gen(None, text="x" * 40)]), run_id="r1")   # 无 usage → chars/4 估算
    assert (c.spent_tokens, c.estimated) == (10, True)


def test_cost_handler_swallows_exceptions():
    from app.agent.llm import CostCallbackHandler
    c = _ctl()
    h = CostCallbackHandler(c)
    h.on_llm_end(object(), run_id="never-started")   # 非法 response / 未知 run_id → 静默
    h.on_llm_end(_Resp([_Gen(None, text="y" * 8)]), run_id="r1")   # 未 start 的 run_id → 静默
    assert c.spent_tokens == 0


def test_classify_accepts_cost_kwarg(monkeypatch):
    """cost 参数默认 None = 零行为变更；传控制器时回调被挂载（不要求真实分类）。"""
    from app.agent import llm as agent_llm

    captured: dict = {}

    class _FakeStructured:
        async def ainvoke(self, messages, config=None):
            captured["config"] = config
            m = agent_llm._IntentSchemaBase(intent="code", needs_collab=False)
            m.response_metadata = {}
            return m

    class _FakeModel:
        def with_structured_output(self, schema):
            return _FakeStructured()

    monkeypatch.setattr("app.core.config.settings.llm_api_key", "ci-dummy")
    monkeypatch.setattr(agent_llm, "configured", lambda: True)
    monkeypatch.setattr(agent_llm, "model_for", lambda purpose="reasoning": _FakeModel())

    import asyncio
    c = _ctl()
    out = asyncio.run(agent_llm.classify_intent_and_collab("查一下这个类", cost=c))
    assert out.intent == "code"
    assert captured["config"] is not None
    # callbacks 列表里含挂到 cost 控制器的 handler
    cbs = (captured["config"] or {}).get("callbacks")
    assert cbs and any(isinstance(cb, agent_llm.CostCallbackHandler) for cb in cbs)
