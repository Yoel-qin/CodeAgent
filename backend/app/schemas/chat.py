"""请求/响应 schema（对齐 api 接口清单）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str
    agent_type: str | None = None
    conversation_id: str | None = None
    module_filter: list[str] | None = None
    code_context: str | None = None
    top_k: int = Field(default=8, ge=1, le=30)
    stream: bool = True
    # Phase 1.5 多模态过滤
    content_type_filter: list[str] | None = None
    file_format_filter: list[str] | None = None


class ResumeRequest(BaseModel):
    """HITL 续跑（M10）：对一条 interrupted 态消息给出人工决策，触发图续跑。"""
    conversation_id: str
    message_id: str
    approved: bool
    comment: str | None = None


class ContinueRequest(BaseModel):
    """通用续跑（M14 Part C）：推进一条已存在 thread 的执行（断流恢复 / 中断态上报）。"""
    conversation_id: str
    message_id: str | None = None  # 若指定，中断态上报时复用此消息 id
