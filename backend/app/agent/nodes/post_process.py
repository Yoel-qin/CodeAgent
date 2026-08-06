"""后处理节点：引用提取 / 格式化定稿钩子。

Milestone 1：引用已在 retrieve 构造、回答已在 generate 产出，此节点为后续 refine /
引用校验 / Orchestrator 结果汇总预留入口，当前透传。
"""
from __future__ import annotations

from app.agent.state import AgentState


async def post_process(state: AgentState) -> dict:
    return {}
