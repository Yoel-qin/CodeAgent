"""全局搜索（⌘K）响应 schema（对齐 api接口清单 §search）。

关键词级 chunk 检索，供前端 ⌘K palette：每条命中含可点击的 chunk_id + 显示用的
label（类.方法 / 文档章节）+ snippet 预览 + 关键词重叠分数。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SearchItem(BaseModel):
    """单条搜索命中。点击时用 chunk_id+kind 聚焦到右侧上下文面板。"""

    chunk_id: str
    kind: Literal["code", "doc"]
    label: str
    snippet: str = ""
    score: float = 0.0


class SearchResponse(BaseModel):
    q: str
    total: int
    items: list[SearchItem]
