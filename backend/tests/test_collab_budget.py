"""M35 多 Agent 协作——预算纯函数 + config/state 地基单测（无需 infra）。"""
from __future__ import annotations

from app.agent.collab import budget, memory  # noqa: F401
from app.core.config import Settings


def test_collab_config_defaults_off():
    s = Settings()
    assert s.multi_agent_collab_enabled is False
    assert s.collab_max_llm_calls == 9
    assert s.collab_max_tool_calls == 12
    assert s.collab_max_rounds_per_layer == 3


def test_collab_state_reducers_accumulate():
    """collab_* 字段经 operator.add 累积（模拟两层节点返回 delta 被 reducer 合并）。"""
    import operator
    import typing

    from app.agent.state import AgentState, _merge_retrieved  # noqa: F401

    # TypedDict 的 __annotations__ 存储 ForwardRef，需先 eval 解析，再用 typing.get_args 取 metadata
    annots = AgentState.__annotations__
    for field in ("tool_steps", "collab_hypotheses", "collab_findings", "collab_suggestions",
                  "collab_llm_calls", "collab_tool_calls"):
        assert field in annots, f"{field} 不在 __annotations__ 中"
        forward_ref = annots[field]
        # 解析 ForwardRef（若为 ForwardRef；已解析的类型直接用）
        if hasattr(forward_ref, "__forward_arg__"):
            globals_dict = {"Annotated": typing.Annotated, "list": list, "dict": dict, "int": int, "operator": operator}
            type_hint = eval(forward_ref.__forward_arg__, globals_dict)
        else:
            type_hint = forward_ref
        # __metadata__ 属性直接给出 metadata 元组
        metadata = getattr(type_hint, "__metadata__", None)
        assert metadata is not None and operator.add in metadata, f"{field} 缺 operator.add reducer（metadata={metadata})"


# ---- budget 纯函数 ----


def test_budget_remaining_and_exhausted():
    assert budget.remaining(3, 9) == 6
    assert budget.remaining(9, 9) == 0
    assert budget.remaining(10, 9) == 0  # 不返负
    assert budget.exhausted(0) is True
    assert budget.exhausted(2) is False


def test_build_collab_report_full():
    hs = [memory.make_hypothesis("消费速度低于生产", confidence="高")]
    fs = [memory.make_finding("c1", "线程池仅 2 核心", verdict="supports")]
    ss = [memory.make_suggestion("扩容消费者线程池", doc_chunk_id="d1")]
    report = budget.build_collab_report(hs, fs, ss)
    assert "消费速度低于生产" in report
    assert "线程池仅 2 核心" in report
    assert "扩容消费者线程池" in report


def test_build_collab_report_empty():
    assert "未能在检索结果中" in budget.build_collab_report([], [], []) or \
           "未产出" in budget.build_collab_report([], [], [])


# ---- memory 构造器 ----


def test_memory_constructors_shape():
    h = memory.make_hypothesis("H", confidence="中", rationale="r")
    assert h == {"hypothesis": "H", "confidence": "中", "rationale": "r"}
    f = memory.make_finding("c1", "F", hypothesis_id=0, verdict="refutes")
    assert f["chunk_id"] == "c1" and f["verdict"] == "refutes"
    s = memory.make_suggestion("S", doc_chunk_id="d1", rationale="rr")
    assert s["suggestion"] == "S" and s["doc_chunk_id"] == "d1"


def test_memory_pydantic_lists_accept_extract():
    hl = memory.HypothesisList(hypotheses=[{"hypothesis": "H", "confidence": "高"}])
    assert hl.hypotheses[0].hypothesis == "H"
    fl = memory.FindingList(findings=[{"chunk_id": "c1", "finding": "F"}])
    assert fl.findings[0].chunk_id == "c1"
    sl = memory.SuggestionList(suggestions=[{"suggestion": "S"}])
    assert sl.suggestions[0].suggestion == "S"
