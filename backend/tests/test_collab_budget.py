"""M35 多 Agent 协作——预算纯函数 + config/state 地基单测（无需 infra）。"""
from __future__ import annotations

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
        assert metadata is not None and operator.add in metadata, f"{field} 缺 operator.add reducer（metadata={metadata}）"
