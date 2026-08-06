"""知识图谱模块路由（api接口清单 §四）：调用图 / 代码-文档关联图 / 模块依赖图 / 图谱节点搜索。

全部只读，走 AsyncSession；空结果返回 ``{nodes:[], edges:[]}`` 而非 404（center 不存在即空态）。
逻辑见 ``services/graph_service.py``。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.graph import (
    CallDirection,
    Granularity,
    GraphResponse,
    GraphSearchResponse,
    NodeKind,
)
from app.services import graph_service

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/call-graph", response_model=GraphResponse)
async def call_graph(
    center_node: str = Query(..., description="中心节点 chunk_id，或 class:{ClassName}"),
    depth: int = Query(2, ge=1, le=5),
    direction: CallDirection = Query("BOTH"),
    max_nodes: int = Query(50, ge=1, le=300),
    session: AsyncSession = Depends(get_db),
) -> GraphResponse:
    """调用图：从 center 沿 call_graph BFS，返回 code 节点 + CALLS 边。"""
    return await graph_service.get_call_graph(
        session, center_node, depth=depth, direction=direction, max_nodes=max_nodes)


@router.get("/code-doc-relations", response_model=GraphResponse)
async def code_doc_relations(
    center_node: str = Query(..., description="中心节点 chunk_id（code 或 doc）"),
    depth: int = Query(1, ge=1, le=3),
    include_stale_only: bool = Query(False),
    max_nodes: int = Query(50, ge=1, le=300),
    session: AsyncSession = Depends(get_db),
) -> GraphResponse:
    """代码-文档关联图：沿 chunk_relations(DOC_TO_CODE/CODE_TO_DOC) 无向 BFS。"""
    return await graph_service.get_code_doc_relations(
        session, center_node, depth=depth,
        include_stale_only=include_stale_only, max_nodes=max_nodes)


@router.get("/module-dependency", response_model=GraphResponse)
async def module_dependency(
    granularity: Granularity = Query("MODULE"),
    session: AsyncSession = Depends(get_db),
) -> GraphResponse:
    """模块依赖图：由 call_graph 按 MODULE/PACKAGE/CLASS 聚合（自环跳过）。"""
    return await graph_service.get_module_dependency(session, granularity=granularity)


@router.get("/search", response_model=GraphSearchResponse)
async def search_nodes(
    q: str = Query(..., min_length=1, description="搜索关键词（类名/方法名/文档内容）"),
    node_type: NodeKind | None = Query(None, description="class/method/doc，缺省全部"),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
) -> GraphSearchResponse:
    """图谱节点搜索：返回可作 center_node 的 id。"""
    return await graph_service.search_graph_nodes(
        session, q, node_type=node_type, limit=limit)
