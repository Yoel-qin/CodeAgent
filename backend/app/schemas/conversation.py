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


class ConversationDetailResponse(BaseModel):
    conversation_id: str
    title: str
    agent_type: str | None = None
    messages: list[MessageItem]


class RetrievalDetailResponse(BaseModel):
    """单条消息的检索漏斗（stage1 召回+RRF / stage2 粗排 / stage3 精排）。"""
    stage1: dict
    stage2: dict
    stage3: dict


class SuggestionRequest(BaseModel):
    conversation_id: str
    last_message_id: str


class SuggestionResponse(BaseModel):
    suggestions: list[str]


class FeedbackRequest(BaseModel):
    rating: str  # HELPFUL | NOT_HELPFUL
    comment: str | None = None
