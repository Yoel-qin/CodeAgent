"""知识图谱查询服务（Phase 4，api接口清单 §四）。

基于已落库的图数据：``call_graph``（方法级调用边）、``chunk_relations``（DOC_TO_CODE/CODE_TO_DOC）、
``code_chunks``/``doc_chunks`` 元数据。**只读，无新迁移**。

分层：纯图算法 helper（无 DB，单测覆盖）+ async 查询层（取 ``AsyncSession``，用 ``text()`` +
``ANY(text[])``，与 ``retrieval/graph_traverse.py`` 同风格）。
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.graph import (
    GraphEdge,
    GraphNode,
    GraphResponse,
    GraphSearchItem,
    GraphSearchResponse,
)

_DOC_REL_TYPES = ("DOC_TO_CODE", "CODE_TO_DOC")
_MODULE_NODE_CAP = 300  # CLASS 粒度可能很大，截断保护


# ============================================================================
# 纯 helper（无 DB，单测覆盖）
# ============================================================================

def aggregate_call_edges(raw: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """对 (caller, callee) 列表去重计权（同对多次调用 = weight）。"""
    weight: dict[tuple[str, str], int] = {}
    for s, t in raw:
        weight[(s, t)] = weight.get((s, t), 0) + 1
    return weight


def group_module_edges(
    call_edges: list[tuple[str, str]],
    group_of: dict[str, str],
    class_of: dict[str, str],
) -> tuple[dict[str, set[str]], dict[tuple[str, str], int]]:
    """按组（MODULE/PACKAGE/CLASS）归并调用边。

    返回 (node_classes: 组→该组 class 集合, weights: (g1,g2)→跨组边数)。
    自环（同组）跳过；两端任一组缺失（None）跳过。node_classes 含所有出现过的组（含仅有自环的）。
    """
    node_classes: dict[str, set[str]] = defaultdict(set)
    weights: dict[tuple[str, str], int] = defaultdict(int)
    for caller, callee in call_edges:
        g1 = group_of.get(caller)
        g2 = group_of.get(callee)
        if g1 is not None and class_of.get(caller):
            node_classes[g1].add(class_of[caller])
        if g2 is not None and class_of.get(callee):
            node_classes[g2].add(class_of[callee])
        if g1 is None or g2 is None or g1 == g2:
            continue
        weights[(g1, g2)] += 1
    return node_classes, dict(weights)


def normalize_doc_edge(
    source: str, target: str, relation_type: str, is_stale: bool, stale_reason: str | None,
) -> tuple[str, str, bool, str | None]:
    """把 DOC_TO_CODE / CODE_TO_DOC 规整为 (code_id, doc_id, stale, reason)。"""
    if relation_type == "DOC_TO_CODE":  # source=doc, target=code
        return target, source, is_stale, stale_reason
    return source, target, is_stale, stale_reason  # CODE_TO_DOC: source=code, target=doc


def _node_name(*parts: str | None) -> str:
    """拼接显示名（跳过 None/空）。"""
    return ".".join(p for p in parts if p) or "?"


# ============================================================================
# async 查询层
# ============================================================================

_TOUCH = text("""SELECT caller_chunk_id, callee_chunk_id FROM call_graph
    WHERE (caller_chunk_id = ANY(cast(:ids as text[]))
           OR callee_chunk_id = ANY(cast(:ids as text[])))
      AND is_deleted = false""")
_SUBGRAPH_EDGES = text("""SELECT caller_chunk_id, callee_chunk_id FROM call_graph
    WHERE caller_chunk_id = ANY(cast(:ids as text[]))
      AND callee_chunk_id = ANY(cast(:ids as text[]))
      AND is_deleted = false""")
_CODE_META = text("""SELECT cc.chunk_id, cc.chunk_type, cc.class_name, cc.method_name,
       cf.module_name, cf.file_path
    FROM code_chunks cc LEFT JOIN code_files cf ON cc.file_id = cf.file_id
    WHERE cc.chunk_id = ANY(cast(:ids as text[])) AND cc.is_deleted = false""")
_CODE_STALE = text("""SELECT DISTINCT chunk_id FROM (
      SELECT source_chunk_id AS chunk_id FROM chunk_relations
        WHERE is_stale AND source_chunk_id = ANY(cast(:ids as text[]))
      UNION
      SELECT target_chunk_id FROM chunk_relations
        WHERE is_stale AND target_chunk_id = ANY(cast(:ids as text[]))
    ) x""")


async def _resolve_code_seeds(session: AsyncSession, center_node: str) -> list[str]:
    """center_node → seed chunk_id 列表。``class:Foo`` 解析为该类所有 chunk_id。"""
    if center_node.startswith("class:"):
        cn = center_node[len("class:"):]
        rows = (await session.execute(text(
            "SELECT chunk_id FROM code_chunks WHERE class_name = :cn AND is_deleted = false"
        ), {"cn": cn})).scalars().all()
        return list(rows)
    exists = (await session.execute(text(
        "SELECT chunk_id FROM code_chunks WHERE chunk_id = :id AND is_deleted = false"
    ), {"id": center_node})).scalar_one_or_none()
    return [center_node] if exists else []


async def get_call_graph(
    session: AsyncSession, center_node: str, *,
    depth: int = 2, direction: str = "BOTH", max_nodes: int = 50,
) -> GraphResponse:
    """调用图：从 center 沿 call_graph BFS（direction 控制方向），返回 code 节点 + CALLS 边。

    两阶段：①逐层用触及 frontier 的边发现节点（按 direction 限制方向）；
    ②一次性取**两端都在 visited 内**的子图边（每条 DB 行=一个 call 位点，不重复计权）。
    """
    seeds = await _resolve_code_seeds(session, center_node)
    if not seeds:
        return GraphResponse(nodes=[], edges=[], center=center_node)

    visited: dict[str, int] = {s: 0 for s in seeds}
    order = list(dict.fromkeys(seeds))
    capped = False
    frontier = list(order)
    for _ in range(max(depth, 0)):
        frontier_set = set(frontier)
        rows = (await session.execute(_TOUCH, {"ids": frontier})).all()
        nxt: list[str] = []
        new: set[str] = set()
        for caller, callee in rows:
            if (direction in ("BOTH", "CALLEES")
                    and caller in frontier_set and callee not in visited and callee not in new):
                if len(visited) >= max_nodes:
                    capped = True
                else:
                    visited[callee] = visited[caller] + 1
                    order.append(callee)
                    nxt.append(callee)
                    new.add(callee)
            if (direction in ("BOTH", "CALLERS")
                    and callee in frontier_set and caller not in visited and caller not in new):
                if len(visited) >= max_nodes:
                    capped = True
                else:
                    visited[caller] = visited[callee] + 1
                    order.append(caller)
                    nxt.append(caller)
                    new.add(caller)
        frontier = nxt
        if not frontier:
            break

    if not order:
        return GraphResponse(nodes=[], edges=[], center=center_node)

    meta = {r[0]: r for r in (await session.execute(_CODE_META, {"ids": order})).all()}
    stale_ids = set((await session.execute(_CODE_STALE, {"ids": order})).scalars().all())

    nodes: list[GraphNode] = []
    for cid in order:
        if (m := meta.get(cid)) is None:
            continue  # 可能被并发删除，跳过
        _, ctype, cls, meth, module, fpath = m
        nodes.append(GraphNode(
            id=cid, name=_node_name(cls, meth) or cid, type=ctype or "code",
            module=module, class_name=cls, method_name=meth, file_path=fpath,
            stale=cid in stale_ids, depth=visited.get(cid),
        ))

    sub_rows = (await session.execute(_SUBGRAPH_EDGES, {"ids": order})).all()
    weights = aggregate_call_edges([(s, t) for s, t in sub_rows])
    edges = [GraphEdge(source=s, target=t, type="CALLS", weight=w) for (s, t), w in weights.items()]
    return GraphResponse(nodes=nodes, edges=edges, center=center_node, truncated=capped)


_DOC_META = text("""SELECT chunk_id, content, heading_path,
       (jsonb_array_length(stale_anchors) > 0) AS stale
    FROM doc_chunks WHERE chunk_id = ANY(cast(:ids as text[])) AND is_deleted = false""")


async def get_code_doc_relations(
    session: AsyncSession, center_node: str, *,
    depth: int = 1, include_stale_only: bool = False, max_nodes: int = 50,
) -> GraphResponse:
    """代码-文档关联图：从 center 沿 chunk_relations(DOC_TO_CODE/CODE_TO_DOC) 无向 BFS。"""
    if not center_node:
        return GraphResponse(nodes=[], edges=[])
    # center 可能是 code 或 doc chunk_id；若不存在直接空
    exists = (await session.execute(text(
        "SELECT chunk_id FROM code_chunks WHERE chunk_id = :id AND is_deleted = false"
    ), {"id": center_node})).scalar_one_or_none()
    if exists is None:
        exists = (await session.execute(text(
            "SELECT chunk_id FROM doc_chunks WHERE chunk_id = :id AND is_deleted = false"
        ), {"id": center_node})).scalar_one_or_none()
    if exists is None:
        return GraphResponse(nodes=[], edges=[], center=center_node)

    visited: dict[str, int] = {center_node: 0}
    order = [center_node]
    touched: list[tuple[str, str, str, bool, str | None]] = []  # (src, tgt, rt, stale, reason)
    capped = False
    frontier = [center_node]
    # IN :types 需展开为元组占位；SQLAlchemy text 不直接支持 tuple IN，改用 ANY
    rel_sql = text("""SELECT source_chunk_id, target_chunk_id, relation_type, is_stale, stale_reason
        FROM chunk_relations
        WHERE (source_chunk_id = ANY(cast(:ids as text[]))
               OR target_chunk_id = ANY(cast(:ids as text[])))
          AND relation_type = ANY(cast(:types as text[]))
          AND (:stale_only = false OR is_stale = true)""")
    for _ in range(max(depth, 0)):
        nxt: list[str] = []
        rows = (await session.execute(rel_sql, {
            "ids": frontier, "types": list(_DOC_REL_TYPES), "stale_only": include_stale_only,
        })).all()
        frontier_set = set(frontier)
        seen_this_level: set[str] = set()
        for src, tgt, rt, is_stale, reason in rows:
            touched.append((src, tgt, rt, bool(is_stale), reason))
            from_node = src if src in frontier_set else tgt
            other = tgt if from_node == src else src
            if other in visited or other in seen_this_level:
                continue
            if len(visited) >= max_nodes:
                capped = True
                continue
            seen_this_level.add(other)
            visited[other] = visited[from_node] + 1
            order.append(other)
            nxt.append(other)
        frontier = nxt
        if not frontier:
            break

    code_ids = [cid for cid in order if not cid.startswith("doc_")]
    doc_ids = [cid for cid in order if cid.startswith("doc_")]
    code_meta = {r[0]: r for r in (await session.execute(_CODE_META, {"ids": code_ids})).all()} if code_ids else {}
    doc_meta = {r[0]: r for r in (await session.execute(_DOC_META, {"ids": doc_ids})).all()} if doc_ids else {}

    # code 节点 stale = 触及该 chunk 的关系有 stale
    code_stale: dict[str, str | None] = {}
    for src, tgt, _rt, is_stale, reason in touched:
        for cid in (src, tgt):
            if cid in code_meta and is_stale:
                code_stale.setdefault(cid, reason)

    nodes: list[GraphNode] = []
    for cid in order:
        if (m := code_meta.get(cid)) is not None:
            _, ctype, cls, meth, module, fpath = m
            nodes.append(GraphNode(
                id=cid, name=_node_name(cls, meth) or cid, type="code",
                module=module, class_name=cls, method_name=meth, file_path=fpath,
                stale=cid in code_stale, stale_reason=code_stale.get(cid), depth=visited.get(cid),
            ))
        elif (d := doc_meta.get(cid)) is not None:
            _, content, heading, stale = d
            hpath = list(heading or [])
            nodes.append(GraphNode(
                id=cid, name=" / ".join(hpath) if hpath else (content[:40] if content else cid),
                type="doc", heading_path=hpath, stale=bool(stale), depth=visited.get(cid),
            ))

    # 规整边为 code→doc，按 (code,doc) 去重合并 stale
    edge_map: dict[tuple[str, str], tuple[bool, str | None]] = {}
    for src, tgt, rt, is_stale, reason in touched:
        if src not in visited or tgt not in visited:
            continue
        code_id, doc_id, stale, rs = normalize_doc_edge(src, tgt, rt, is_stale, reason)
        prev = edge_map.get((code_id, doc_id))
        if prev is None or (stale and not prev[0]):
            edge_map[(code_id, doc_id)] = (stale, rs)
    edges = [GraphEdge(source=c, target=d, type="DOCUMENTED_BY",
                       stale=s, stale_reason=r) for (c, d), (s, r) in edge_map.items()]
    return GraphResponse(nodes=nodes, edges=edges, center=center_node, truncated=capped)


async def get_module_dependency(
    session: AsyncSession, granularity: str = "MODULE",
) -> GraphResponse:
    """模块依赖图：由 call_graph 边按 MODULE/PACKAGE/CLASS 聚合（自环跳过）。"""
    edges_rows = (await session.execute(text(
        "SELECT caller_chunk_id, callee_chunk_id FROM call_graph WHERE is_deleted = false"
    ))).all()
    chunk_rows = (await session.execute(text(
        "SELECT chunk_id, class_name, file_id FROM code_chunks WHERE is_deleted = false"
    ))).all()
    file_rows = (await session.execute(text(
        "SELECT file_id, module_name, package_name FROM code_files WHERE is_deleted = false"
    ))).all()

    file_meta = {r[0]: r for r in file_rows}
    group_of: dict[str, str] = {}
    class_of: dict[str, str] = {}
    for cid, cls, fid in chunk_rows:
        fm = file_meta.get(fid)
        if granularity == "CLASS":
            g = cls or "(unknown)"
        elif granularity == "PACKAGE":
            g = (fm[2] if fm else None) or "(unknown)"  # package_name
        else:  # MODULE
            g = (fm[1] if fm else None) or "(unknown)"  # module_name
        group_of[cid] = g
        if cls:
            class_of[cid] = cls

    node_classes, weights = group_module_edges([(c, e) for c, e in edges_rows], group_of, class_of)

    # 节点 = 所有出现过的组（含仅自环）
    all_groups: set[str] = set(node_classes.keys())
    for g1, g2 in weights:
        all_groups.add(g1)
        all_groups.add(g2)

    nodes = [GraphNode(id=g, name=g, type=granularity.lower(),
                       class_count=len(node_classes.get(g, ()))) for g in sorted(all_groups)]
    edge_list = [GraphEdge(source=g1, target=g2, type="DEPENDS_ON", weight=w)
                 for (g1, g2), w in weights.items()]

    truncated = False
    if granularity == "CLASS" and (len(nodes) > _MODULE_NODE_CAP or len(edge_list) > _MODULE_NODE_CAP):
        truncated = True
        edge_list.sort(key=lambda e: e.weight, reverse=True)
        kept_edges = edge_list[:_MODULE_NODE_CAP]
        kept_groups = {e.source for e in kept_edges} | {e.target for e in kept_edges}
        nodes = [n for n in nodes if n.id in kept_groups]
        edge_list = kept_edges
    return GraphResponse(nodes=nodes, edges=edge_list, truncated=truncated)


async def search_graph_nodes(
    session: AsyncSession, q: str, *, node_type: str | None = None, limit: int = 10,
) -> GraphSearchResponse:
    """图谱节点搜索：class（按类名聚合）/ method / doc。返回可作 center_node 的 id。"""
    pat = f"%{q}%"
    items: list[GraphSearchItem] = []
    want = node_type or "all"

    if want in ("all", "class"):
        rows = (await session.execute(text("""
            SELECT DISTINCT ON (cc.class_name) cc.class_name, cf.module_name, cf.file_path
            FROM code_chunks cc LEFT JOIN code_files cf ON cc.file_id = cf.file_id
            WHERE cc.is_deleted = false AND cc.class_name ILIKE :q
            ORDER BY cc.class_name LIMIT :n
        """), {"q": pat, "n": limit})).all()
        for cls, module, fpath in rows:
            items.append(GraphSearchItem(
                id=f"class:{cls}", name=cls or "?", type="class",
                module=module, class_name=cls, file_path=fpath))

    if want in ("all", "method"):
        rows = (await session.execute(text("""
            SELECT cc.chunk_id, cc.class_name, cc.method_name, cf.module_name, cf.file_path
            FROM code_chunks cc LEFT JOIN code_files cf ON cc.file_id = cf.file_id
            WHERE cc.is_deleted = false AND cc.chunk_type = 'method'
              AND (cc.method_name ILIKE :q OR cc.class_name ILIKE :q)
            LIMIT :n
        """), {"q": pat, "n": limit})).all()
        for cid, cls, meth, module, fpath in rows:
            items.append(GraphSearchItem(
                id=cid, name=_node_name(cls, meth) or cid, type="method",
                module=module, class_name=cls, file_path=fpath))

    if want in ("all", "doc"):
        rows = (await session.execute(text("""
            SELECT chunk_id, heading_path FROM doc_chunks
            WHERE is_deleted = false AND content ILIKE :q LIMIT :n
        """), {"q": pat, "n": limit})).all()
        for cid, heading in rows:
            hpath = list(heading or [])
            items.append(GraphSearchItem(
                id=cid, name=" / ".join(hpath) if hpath else cid,
                type="doc", heading_path=hpath))

    return GraphSearchResponse(items=items)
