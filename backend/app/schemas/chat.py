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
