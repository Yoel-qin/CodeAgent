"""调用图读 API（M6 Task 2）：/v1/graph 三端点，纯只读、形状冻结。

session 用法沿 documents.py（``async with SessionLocal()``）；SQL 全部在
graph_service，路由只做参数边界与透传。参数边界在 Query 上声明：
direction Literal 三值、depth 1..5、max_nodes 上限 300、search limit 1..50、
q 必填非空——越界/缺失 → 422（防巨 depth/max_nodes 打爆 PG）。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_class
from app.db.base import SessionLocal
from app.services import graph_service

router = APIRouter(prefix="/v1/graph", tags=["graph"], dependencies=[Depends(require_class("graph"))])


@router.get("/search")
async def search_entities(
    q: str = Query(..., min_length=1),
    repo: str = Query(...),
    limit: int = Query(default=15, ge=1, le=50),
) -> dict:
    """实体搜索（类名/方法名子串，方法实体排前）→ ``{"items": [...]}``。"""
    async with SessionLocal() as session:
        return await graph_service.search_entities(session, q=q, repo=repo, limit=limit)


@router.get("/call-graph")
async def call_graph(
    repo: str = Query(...),
    class_name: str = Query(...),
    method: str | None = Query(default=None),
    direction: Literal["BOTH", "CALLERS", "CALLEES"] = Query(default="BOTH"),
    depth: int = Query(default=2, ge=1, le=5),
    max_nodes: int = Query(default=50, ge=1, le=300),
) -> dict:
    """类/方法中心调用图（``{"nodes","edges","center","truncated"}``）。"""
    async with SessionLocal() as session:
        return await graph_service.call_graph(
            session, repo=repo, class_name=class_name, method=method,
            direction=direction, depth=depth, max_nodes=max_nodes,
        )


@router.get("/module-deps")
async def module_deps(
    repo: str = Query(...),
    max_nodes: int = Query(default=60, ge=1, le=300),
) -> dict:
    """跨 module 聚合调用图（nodes type=module、edges weight=调用数）。"""
    async with SessionLocal() as session:
        return await graph_service.module_deps_graph(session, repo=repo, max_nodes=max_nodes)
