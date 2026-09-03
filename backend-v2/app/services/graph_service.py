"""调用图读服务（M6 Task 2）：递归 CTE 出图，组装 GraphPage 冻结响应形状。

只读、纯 PG（``code_entities`` / ``call_edges``），session 由路由层注入。响应形状冻结见
Plan 4 Global Constraints「GraphPage 响应形状冻结」（v1 ``api/graph.ts`` 的 ``GraphResponse``
兼容、``CytoscapeGraph.tsx`` 零改复用）：

- node：``{"id": str(entity_id), "name": "Class#method"|类名|模块名, "type": "method"|"class"|"module",
  "class_name"?, "method_name"?, "module"?, "file_path"?}`` —— ``type`` 由 ``method_name``
  是否为 NULL 推导（``entity_type`` 里的 interface/enum/record 等冻结形状只允许归并为 class）。
- edge：``{"source": id, "target": id, "type": "CALLS", "weight": int}``（同一 (source,target)
  的多条调用边合并、weight=调用次数——Cytoscape 元素 id 由 (source,target) 拼出，不可重复）。
- 顶层：``{"nodes": [...], "edges": [...], "center": id|null, "truncated": bool}``。

递归 CTE 沿 ``app/core/graph_query.py``（MCP graph server）同款 visited 数组防环，此处
方向参数化为两条镜像 SQL：CALLEES 种子在 caller 侧、沿 ``caller_id = ch.callee_id`` 扩；
CALLERS 种子在 callee 侧、沿 ``callee_id = ch.caller_id`` 扩。BOTH = 两方向各跑一次后
边集/节点集合并。节点集 = 种子 + 边两端实体；超 max_nodes 截断 + ``truncated=True``。
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Direction = Literal["BOTH", "CALLERS", "CALLEES"]

# 种子实体（类中心 method=None → 该类全部方法实体；方法中心 → 唯一方法实体）。
# CAST 显式定型 :m（asyncpg 对「:m IS NULL OR col = :m」报 AmbiguousParameterError；
# 不能写 :m::VARCHAR——SQLAlchemy bind 正则不识别后随 "::" 的参数名）。两条 CTE 同款。
_SEED_SQL = """
SELECT id, class_name, method_name, module, file_path
FROM code_entities
WHERE repo = :repo AND class_name = :cls
  AND (CAST(:m AS VARCHAR) IS NULL OR method_name = :m) AND method_name IS NOT NULL
ORDER BY id
"""

# CALLEES（下游）：种子在 caller 侧，沿 caller → callee 扩。
_CALLEES_CTE = """
WITH RECURSIVE chain AS (
    SELECT e.id AS edge_id, e.caller_id, e.callee_id, 1 AS depth,
           ARRAY[e.caller_id] AS visited
    FROM call_edges e
    JOIN code_entities c ON c.id = e.caller_id
    WHERE c.repo = :repo AND c.class_name = :cls
      AND (CAST(:m AS VARCHAR) IS NULL OR c.method_name = :m) AND c.method_name IS NOT NULL
    UNION ALL
    SELECT e.id, e.caller_id, e.callee_id, ch.depth + 1,
           ch.visited || e.callee_id
    FROM call_edges e
    JOIN chain ch ON e.caller_id = ch.callee_id
    WHERE ch.depth < :depth AND NOT (e.callee_id = ANY(ch.visited))
)
SELECT ch.edge_id, a.id AS caller_id, a.class_name AS caller_class,
       a.method_name AS caller_method, a.module AS caller_module,
       a.file_path AS caller_file,
       b.id AS callee_id, b.class_name AS callee_class,
       b.method_name AS callee_method, b.module AS callee_module,
       b.file_path AS callee_file
FROM chain ch
JOIN code_entities a ON a.id = ch.caller_id
JOIN code_entities b ON b.id = ch.callee_id
ORDER BY ch.depth, ch.edge_id
"""

# CALLERS（上游）：种子在 callee 侧，沿 callee ← caller 扩（与 CALLEES 逐字段镜像）。
_CALLERS_CTE = """
WITH RECURSIVE chain AS (
    SELECT e.id AS edge_id, e.caller_id, e.callee_id, 1 AS depth,
           ARRAY[e.callee_id] AS visited
    FROM call_edges e
    JOIN code_entities c ON c.id = e.callee_id
    WHERE c.repo = :repo AND c.class_name = :cls
      AND (CAST(:m AS VARCHAR) IS NULL OR c.method_name = :m) AND c.method_name IS NOT NULL
    UNION ALL
    SELECT e.id, e.caller_id, e.callee_id, ch.depth + 1,
           ch.visited || e.caller_id
    FROM call_edges e
    JOIN chain ch ON e.callee_id = ch.caller_id
    WHERE ch.depth < :depth AND NOT (e.caller_id = ANY(ch.visited))
)
SELECT ch.edge_id, a.id AS caller_id, a.class_name AS caller_class,
       a.method_name AS caller_method, a.module AS caller_module,
       a.file_path AS caller_file,
       b.id AS callee_id, b.class_name AS callee_class,
       b.method_name AS callee_method, b.module AS callee_module,
       b.file_path AS callee_file
FROM chain ch
JOIN code_entities a ON a.id = ch.caller_id
JOIN code_entities b ON b.id = ch.callee_id
ORDER BY ch.depth, ch.edge_id
"""

# 跨 module 聚合调用边（module-deps 图：nodes=module、weight=调用数）。
_MODULE_DEPS_SQL = """
SELECT a.module AS src_module, b.module AS dst_module, COUNT(*) AS call_count
FROM call_edges e
JOIN code_entities a ON a.id = e.caller_id
JOIN code_entities b ON b.id = e.callee_id
WHERE a.repo = :repo AND b.repo = :repo AND a.module <> b.module
GROUP BY a.module, b.module
ORDER BY call_count DESC, a.module, b.module
"""

# 节点/实体搜索（类名或方法名子串，方法实体排前）。
_SEARCH_SQL = """
SELECT id, class_name, method_name, module, file_path
FROM code_entities
WHERE repo = :repo AND (class_name ILIKE :pat OR method_name ILIKE :pat)
ORDER BY (method_name IS NULL), class_name
LIMIT :limit
"""


def _node(entity_id: int, class_name: str, method_name: str | None,
          module: str, file_path: str) -> dict:
    """实体行 → 冻结形状 node dict（type 由 method_name 推导，见模块 docstring）。"""
    return {
        "id": str(entity_id),
        "name": f"{class_name}#{method_name}" if method_name else class_name,
        "type": "method" if method_name else "class",
        "class_name": class_name,
        "method_name": method_name,
        "module": module,
        "file_path": file_path,
    }


# direction → 该方向要跑的 CTE（BOTH = 两方向各跑一次，边/节点在 Python 侧合并去重）
_DIRECTION_SQLS: dict[str, tuple[str, ...]] = {
    "CALLEES": (_CALLEES_CTE,),
    "CALLERS": (_CALLERS_CTE,),
    "BOTH": (_CALLEES_CTE, _CALLERS_CTE),
}


async def call_graph(
    session: AsyncSession,
    *,
    repo: str,
    class_name: str,
    method: str | None = None,
    direction: Direction = "BOTH",
    depth: int = 2,
    max_nodes: int = 50,
) -> dict:
    """类/方法为中心的调用图（种子 + depth 跳内边两端实体）。

    center：唯一种子（方法中心恒一；类中心恰好一个方法实体）= 该种子 id，
    多种子/无种子 = ``None``（多种子无唯一中心，形状允许 null）。
    """
    params: dict = {"repo": repo, "cls": class_name, "m": method, "depth": depth}
    seed_rows = (await session.execute(text(_SEED_SQL), params)).mappings().all()

    nodes: dict[str, dict] = {}
    order: list[str] = []  # 种子在前、边端点按遍历序，截断顺序确定

    def _add(entity_id: int, cls: str, m: str | None, module: str, file_path: str) -> str:
        nid = str(entity_id)
        if nid not in nodes:
            nodes[nid] = _node(entity_id, cls, m, module, file_path)
            order.append(nid)
        return nid

    for r in seed_rows:
        _add(r["id"], r["class_name"], r["method_name"], r["module"], r["file_path"])

    weights: dict[tuple[str, str], int] = {}  # (source, target) → 调用次数
    for sql in _DIRECTION_SQLS[direction]:
        rows = (await session.execute(text(sql), params)).mappings().all()
        for r in rows:  # 同一边经多条路径可达会重复出现，按 (source, target) 合并
            src = _add(r["caller_id"], r["caller_class"], r["caller_method"],
                       r["caller_module"], r["caller_file"])
            dst = _add(r["callee_id"], r["callee_class"], r["callee_method"],
                       r["callee_module"], r["callee_file"])
            weights[(src, dst)] = weights.get((src, dst), 0) + 1

    truncated = len(order) > max_nodes
    if truncated:  # 超出 max_nodes：按遍历序保留前 max_nodes 个节点，边随节点裁剪
        keep = set(order[:max_nodes])
        order = order[:max_nodes]
        nodes = {nid: nodes[nid] for nid in order}
        weights = {k: v for k, v in weights.items() if k[0] in keep and k[1] in keep}

    edges = [
        {"source": src, "target": dst, "type": "CALLS", "weight": w}
        for (src, dst), w in weights.items()
    ]
    center = str(seed_rows[0]["id"]) if len(seed_rows) == 1 else None
    return {"nodes": list(nodes.values()), "edges": edges, "center": center,
            "truncated": truncated}


async def search_entities(
    session: AsyncSession, *, q: str, repo: str, limit: int = 15
) -> dict:
    """实体搜索（class_name / method_name ILIKE 子串，方法实体排前）。"""
    rows = (await session.execute(text(_SEARCH_SQL),
                                  {"repo": repo, "pat": f"%{q}%", "limit": limit})).mappings().all()
    return {
        "items": [
            {
                "id": str(r["id"]),
                "name": f"{r['class_name']}#{r['method_name']}" if r["method_name"]
                else r["class_name"],
                "type": "method" if r["method_name"] else "class",
                "module": r["module"],
                "class_name": r["class_name"],
                "file_path": r["file_path"],
            }
            for r in rows
        ]
    }


async def module_deps_graph(
    session: AsyncSession, *, repo: str, max_nodes: int = 60
) -> dict:
    """跨 module 聚合调用图：nodes=module、edges weight=调用数、center 恒 None。"""
    rows = (await session.execute(text(_MODULE_DEPS_SQL), {"repo": repo})).mappings().all()

    nodes: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        for key in ("src_module", "dst_module"):
            name = r[key]
            if name not in nodes:
                nodes[name] = {"id": name, "name": name, "type": "module"}
                order.append(name)
    truncated = len(order) > max_nodes
    if truncated:
        keep = set(order[:max_nodes])
        order = order[:max_nodes]
        nodes = {name: nodes[name] for name in order}
        rows = [r for r in rows if r["src_module"] in keep and r["dst_module"] in keep]

    edges = [
        {"source": r["src_module"], "target": r["dst_module"], "type": "CALLS",
         "weight": r["call_count"]}
        for r in rows
    ]
    return {"nodes": list(nodes.values()), "edges": edges, "center": None,
            "truncated": truncated}
