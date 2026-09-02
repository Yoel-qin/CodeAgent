"""Task 8：CostController 移植语义回归（brief 逐字）。"""
import pytest

from app.agent.cost import BudgetExceeded, CostController


def test_cost_marks_then_raises():
    c = CostController(max_tokens=100, max_llm_calls=2)
    c.record_call()
    c.record_call()
    c.record_call()
    assert c.exceeded is not None and c.llm_calls == 3
    with pytest.raises(BudgetExceeded):
        c.check()
    assert "预算上限" in c.exceeded.notice()


def test_cost_usage_accumulates():
    c = CostController(max_tokens=10, max_llm_calls=9)
    c.record_usage(prompt=4, completion=4)
    c.record_usage(prompt=4, completion=4, estimated=True)
    assert c.spent_tokens == 16 and c.estimated is True
