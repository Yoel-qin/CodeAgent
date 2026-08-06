"""会话/检索详情/追问/反馈 的请求响应 schema（对齐 api接口清单 §2.2–2.6）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ConversationItem(BaseModel):
    conversation_id: str
    title: str
    agent_type: str | None = None
    message_count: int
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    total: int
    items: list[ConversationItem]


class MessageItem(BaseModel):
    message_id: str
    role: str
    content: str
    citations: list[dict] | None = None
    agent_type: str | None = None
    created_at: datetime
    # M14：暴露状态（completed | interrupted | expired），让前端可见待审批/已过期（默认 completed 保零回归）。
    status: str = "completed"


class ConversationDetailResponse(BaseModel):
    conversation_id: str
    title: str
    agent_type: str | None = None
    messages: list[MessageItem]


class InterruptInfo(BaseModel):
    """待审批 interrupt 的快照信息（GET /conversations/{id}/state，M14 Part C）。"""

    proposal: str | None = None
    message_id: str | None = None
    created_at: datetime | None = None
    age_hours: float | None = None


class ThreadStateResponse(BaseModel):
    """一条会话线程的当前执行状态（HITL 可观测，M14 Part C）。"""

    conversation_id: str
    status: str | None = None  # 最新 assistant 消息状态（completed | interrupted | expired）
    has_pending_interrupt: bool
    interrupt: InterruptInfo | None = None


class AgentTraceResponse(BaseModel):
    """场景 Agent 的工具调用轨迹（mode:agent 路径；legacy/retrieve 路径不返回此段）。"""

    type: str  # agent_type，如 "CHANGE_IMPACT"
    steps: list[dict]  # [{tool, args, n}, ...]，原始 agent_step 事件 data


class RetrievalDetailResponse(BaseModel):
    """单条消息的检索漏斗（stage1 召回+RRF / stage2 粗排 / stage3 精排）。

    Agent 作答的消息额外带 ``agent`` 段（工具调用轨迹），便于回放 Agent 推理过程。
    """

    stage1: dict
    stage2: dict
    stage3: dict
    agent: AgentTraceResponse | None = None


class SuggestionRequest(BaseModel):
    conversation_id: str
    last_message_id: str


class SuggestionResponse(BaseModel):
    suggestions: list[str]


class FeedbackRequest(BaseModel):
    rating: str  # HELPFUL | NOT_HELPFUL
    comment: str | None = None
