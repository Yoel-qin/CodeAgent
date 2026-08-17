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
