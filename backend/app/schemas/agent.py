"""Agent 面板（/agents）的响应 schema。

数据全部来自 ``retrieval_logs`` 按需聚合（无新表）：``mode:'agent'`` = Agent 成功跑完，
``agent_steps`` JSONB = 工具轨迹，``user_feedback`` = 满意度，降级 = ``agent_steps`` 非空但 ``mode`` 缺失。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

# ---- GET /stats ----


class AgentStatRow(BaseModel):
    """单个 Agent 的聚合行（按 agent 标签 GROUP BY）。"""

    agent: str
    calls: int
    avg_steps: float | None
    hit_rate: float | None  # 产出引用(fine_rank_count>0)的 run 占比
    satisfaction: float | None  # HELPFUL / 已反馈
    degraded: int  # 部分失败降级数（agent_steps 非空且 mode 缺失）


class AgentStatsResponse(BaseModel):
    """Agent 面板 KPI + 各 Agent 明细。"""

    window: str  # today / 7d / all
    total_calls: int  # 面板1：Agent 成功 run（mode='agent'）
    engaged: int  # 降级率分母：mode='agent' 或 agent_steps 非空
    degraded: int
    degradation_rate: float | None  # 面板5
    avg_steps: float | None  # 面板4
    helpful: int
    feedback: int
    satisfaction: float | None  # 面板2
    per_agent: list[AgentStatRow]  # 面板3


# ---- GET /runs ----


class AgentRunItem(BaseModel):
    """单条 Agent 运行流水。"""

    log_id: int
    created_at: datetime
    agent: str | None  # 降级 run 经 chat_messages.agent_type 回退
    query: str
    steps: int
    citations: int
    degraded: bool
    feedback: str | None


class AgentRunsResponse(BaseModel):
    total: int
    items: list[AgentRunItem]
