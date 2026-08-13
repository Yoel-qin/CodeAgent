"""M37 领域 Agent 的 prompt 组装 + pack 解析（纯函数，无 LangGraph/DB 依赖）。

三个领域 agent（trace/diagnose/tune）的 prompt = 代码内 base 角色 + 激活包注入的领域知识。
pack 经 ``_pack_from_state`` 从 state 的 active_pack_name 经 DomainPackRegistry 解析。
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from app.domain_packs.models import DomainPack
from app.domain_packs.registry import get_registry as _get_pack_registry

_BASE_ROLE: dict[str, str] = {
    "trace": (
        "你是 CodeRAG 的【链路追踪 Agent】，擅长梳理某场景的完整方法调用链路（如消息发送、消费、"
        "事务、定时等端到端流程）。工作方式（ReAct）：先用工具定位入口方法，再沿调用链展开（谁调用谁、"
        "调用链路），观察结果，最后按场景模板产出完整方法序列。"
        "可用工具：search_code（语义检索）、search_symbol（按名解析 id）、get_call_chain（展开双向调用链）、"
        "get_downstream_callers（下游依赖）、read_code（精读实现）。"
        "规则：① 链路必须基于工具检索到的真实代码，不要臆造方法名；② 引用用 chunk_id 或 类名.方法名；"
        "③ 2-4 步工具调用即可，按下方【领域链路模板】对齐场景；④ 中文、简洁，方法序列用有序列表/代码块。"
    ),
    "diagnose": (
        "你是 CodeRAG 的【故障诊断 Agent】，擅长按诊断决策树排查中间件故障症状的根因（如消息堆积、"
        "丢失、rebalance 异常、消费停滞）。工作方式（ReAct）：先识别症状对应的诊断树，再用工具定位"
        "相关代码（生产者/消费者/broker 配置与实现）、查近期变更，按决策树的假设逐步验证，最后给根因。"
        "可用工具：search_code、search_symbol、get_callers（上游影响）、get_recent_changes（回归排查）、"
        "read_code、get_related_docs（关联文档）。"
        "规则：① 根因必须基于工具检索到的代码/变更记录，不要臆造；② 按【领域诊断决策树】的假设逐条验证；"
        "③ 引用用 chunk_id 或 类名.方法名；④ 中文、简洁，根因+证据+建议分明。"
    ),
    "tune": (
        "你是 CodeRAG 的【性能调优 Agent】，擅长按调优规则给配置/参数性能建议（高吞吐、低延迟等场景）。"
        "工作方式（ReAct）：先用工具定位相关代码与度量（复杂度/调用频度）、查近期变更，再按调优规则匹配场景，"
        "给出参数调整建议与权衡。"
        "可用工具：search_code、search_symbol、get_code_metrics（LOC/fan-in/fan-out）、"
        "get_recent_changes、read_code。"
        "规则：① 建议必须基于工具检索到的代码度量与【领域调优规则】，不要臆造参数；② 给出参数、建议值、"
        "权衡（tradeoff）；③ 引用用 chunk_id 或 类名.方法名；④ 中文、简洁。"
    ),
}

# agent_name → (pack 字段名, prompt 段标题)
_KNOWLEDGE_FIELDS: dict[str, tuple[str, str]] = {
    "trace": ("trace_templates", "领域链路模板"),
    "diagnose": ("diagnosis_trees", "领域诊断决策树"),
    "tune": ("tuning_rules", "领域调优规则"),
}


def _serialize_pack_knowledge(agent_name: str, pack: DomainPack) -> str:
    """把 pack 对应字段序列化为 prompt 文本段；空列表/未知 agent_name → ""。"""
    field_header = _KNOWLEDGE_FIELDS.get(agent_name)
    if field_header is None:
        return ""
    field, header = field_header
    items = getattr(pack, field)
    if not items:
        return ""
    lines = [f"=== {header}（来自激活包 {pack.manifest.target_repo}）==="]
    for it in items:
        lines.append(json.dumps(it.model_dump(), ensure_ascii=False, indent=2))
    return "\n".join(lines)


def build_domain_prompt(agent_name: str, pack: DomainPack | None) -> str:
    """组装领域 agent prompt = base 角色 [+ pack.prompts[agent]] [+ pack 领域知识序列化]。

    pack=None → 仅 base 角色（容错；正常路径 route 守卫已挡，不应进领域节点）。
    """
    parts: list[str] = [_BASE_ROLE[agent_name]]
    if pack is not None:
        domain_hint = pack.prompts.get(agent_name)
        if domain_hint:
            parts.append(domain_hint)
        knowledge = _serialize_pack_knowledge(agent_name, pack)
        if knowledge:
            parts.append(knowledge)
    return "\n\n".join(parts)


def _pack_from_state(state: Mapping) -> DomainPack | None:
    """state["active_pack_name"] → DomainPack（经 DomainPackRegistry 单例）；None/缺失/找不到 → None。"""
    name = state.get("active_pack_name")
    if not name:
        return None
    return _get_pack_registry().get(name)
