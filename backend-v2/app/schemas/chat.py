"""Chat 请求模型（Plan 3 Task 9）。

``conversation_id`` 在 API 边界校验 32 位 hex（Task 2 评审遗留）——``open_conversation``
对不存在的 id 会沿用新建，脏值（带空格/超长/大写 hex）若无此闸会直接落 PG 主键。
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

__all__ = ["ChatRequest"]

_CID_RE = re.compile(r"^[0-9a-f]{32}$")


class ChatRequest(BaseModel):
    """POST /v1/chat/completions 请求体（SSE 流式应答）。"""

    query: str = Field(min_length=1)
    conversation_id: str | None = None
    repo: str | None = None
    top_k: int = 8

    @field_validator("conversation_id")
    @classmethod
    def _cid_is_hex32(cls, v: str | None) -> str | None:
        if v is not None and not _CID_RE.match(v):
            raise ValueError("conversation_id 须为 32 位小写 hex（uuid4().hex）")
        return v
