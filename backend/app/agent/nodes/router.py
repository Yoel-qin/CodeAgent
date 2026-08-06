"""Orchestrator 条件路由：意图→Agent 分发表（多 Agent 编排）。

按意图（或显式 agent_type）自动路由到对应场景 Agent，否则走 retrieve 兜底。
**加一个场景 Agent = 两张表各加一行 + graph.py 加节点/边**（无需改本文件逻辑）。

设计 §3/§4 的 router 条件边。未配置 LLM 时不进任何 Agent（走 retrieve→generate，其自身降级）。
"""
from __future__ import annotations

from app.agent.llm import configured
from app.agent.state import AgentState

#: 显式 agent_type 值 → 主图节点名
_AGENT_TYPE_TO_NODE = {
    "CODE_UNDERSTAND": "code_understand",
    "DOC_ANSWER": "doc_answer",
    "CHANGE_IMPACT": "change_impact",
    "BUG_DIAGNOSIS": "bug_diagnosis",
    "CODE_REVIEW": "code_review",
    "TEST_GENERATION": "test_generation",
    "DOC_MAINTAIN": "propose",  # HITL 分支入口（M10）：propose→confirm→apply|reject
}

#: 意图标签 → agent_type（无显式 agent_type 时按意图选 Agent）
# 注：graph 意图（依赖/结构/影响范围）→ 变更影响 Agent（用调用图工具作答）；
#     bug 意图（报错/异常/崩溃/为何失败）→ 缺陷诊断 Agent（含回归排查）；
#     review 意图（代码审查/质量评估/改进建议）→ 代码审查 Agent（含量化度量）；
#     test 意图（生成/写单元测试）→ 测试生成 Agent（对齐项目测试约定生成 JUnit）。
_INTENT_TO_AGENT_TYPE = {
    "code": "CODE_UNDERSTAND",
    "doc": "DOC_ANSWER",
    "graph": "CHANGE_IMPACT",
    "bug": "BUG_DIAGNOSIS",
    "review": "CODE_REVIEW",
    "test": "TEST_GENERATION",
}


def route(state: AgentState) -> str:
    """返回下一节点名：某场景 Agent 节点 | retrieve（兜底）。

    优先级：显式 agent_type > 意图；未配置 LLM 或无匹配意图 → retrieve。
    """
    if not configured():
        return "retrieve"
    agent_type = state.get("agent_type") or _INTENT_TO_AGENT_TYPE.get(state.get("intent", ""))
    return _AGENT_TYPE_TO_NODE.get(agent_type, "retrieve")
