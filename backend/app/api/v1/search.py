"""全局搜索路由（api接口清单 §search）：关键词级 chunk 检索，供前端 ⌘K palette。

纯 PG（``lexical_recall``），零 API key、零向量——按键场景要快；语义检索走 ``/v1/chat``。
逻辑见 ``services/search_service.py``；空 ``q`` 由 FastAPI 422 拦截。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.search import SearchResponse
from app.services import search_service

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    kind: Literal["code", "doc"] | None = Query(None, description="限定 code/doc，缺省全部"),
    top_k: int = Query(12, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """全局关键词搜索：返回 code+doc chunk（含 label/snippet/score），供 ⌘K 跳转/聚焦。"""
    data = await search_service.search(session, q, kind=kind, top_k=top_k)
    return SearchResponse(**data)
