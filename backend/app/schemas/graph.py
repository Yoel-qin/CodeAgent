"""知识图谱模块请求/响应 schema（Phase 4，对齐 api接口清单 §四）。

图节点/边为通用结构，由 4 个端点复用：
- ``call-graph``：code 节点（method/class）+ CALLS 边
- ``code-doc-relations``：code + doc 节点 + DOCUMENTED_BY 边（含 stale 标记）
- ``module-dependency``：module/package/class 聚合节点 + DEPENDS_ON 边（weight=跨组调用边数）
- ``search``：图谱节点搜索（返回可作 center_node 的 id）
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class GraphNode(BaseModel):
    """图节点。``type`` ∈ method/class/code/doc/module/package。"""
    id: str
    name: str
    type: str
    module: str | None = None
    class_name: str | None = None
    method_name: str | None = None
    file_path: str | None = None
    heading_path: list[str] = []
    stale: bool = False
    stale_reason: str | None = None
    class_count: int | None = None  # module/package 节点：组内不同 class 数
    depth: int | None = None  # 距 center 的 BFS 跳数（call-graph / code-doc）


class GraphEdge(BaseModel):
    """图边。``type`` ∈ CALLS / DOCUMENTED_BY / DEPENDS_ON。"""
    source: str
    target: str
    type: str
    weight: int = 1
    stale: bool = False
    stale_reason: str | None = None


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    center: str | None = None
    truncated: bool = False  # 达到 max_nodes 被截断


class GraphSearchItem(BaseModel):
    id: str  # method/doc → chunk_id；class → ``class:{ClassName}``
    name: str
    type: str  # class / method / doc
    module: str | None = None
    class_name: str | None = None
    file_path: str | None = None
    heading_path: list[str] = []


class GraphSearchResponse(BaseModel):
    items: list[GraphSearchItem]


# ---- 查询参数枚举（文档化，路由侧用 Literal 校验） ----

CallDirection = Literal["BOTH", "CALLERS", "CALLEES"]
Granularity = Literal["MODULE", "PACKAGE", "CLASS"]
NodeKind = Literal["class", "method", "doc"]
