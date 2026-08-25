"""图遍历召回（设计 §11.3 路径 D）：从种子节点沿 call_graph + chunk_relations BFS 扩展。
M32 ②：增加关系类型过滤（calls/implements/extends/doc_anchor）。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_NEIGHBOR_SQL = text("""
    SELECT callee_chunk_id AS n FROM call_graph
      WHERE caller_chunk_id = ANY(cast(:ids as text[])) AND is_deleted = false
    UNION
    SELECT caller_chunk_id AS n FROM call_graph
      WHERE callee_chunk_id = ANY(cast(:ids as text[])) AND is_deleted = false
    UNION
    SELECT target_chunk_id AS n FROM chunk_relations
      WHERE source_chunk_id = ANY(cast(:ids as text[])) AND is_stale = false
    UNION
    SELECT source_chunk_id AS n FROM chunk_relations
      WHERE target_chunk_id = ANY(cast(:ids as text[])) AND is_stale = false
""")

# M32 ②：关系类型过滤——calls 走 call_graph 表；implements/extends/doc_anchor 走
# chunk_relations.relation_type 过滤（DOC_TO_CODE/CODE_TO_DOC/CODE_IMPLEMENTS/CODE_EXTENDS）。
_CALLS_SQL = text("""
    SELECT callee_chunk_id AS n FROM call_graph
      WHERE caller_chunk_id = ANY(cast(:ids as text[])) AND is_deleted = false
    UNION
    SELECT caller_chunk_id AS n FROM call_graph
      WHERE callee_chunk_id = ANY(cast(:ids as text[])) AND is_deleted = false
""")

_TYPED_SQL = text("""
    SELECT target_chunk_id AS n FROM chunk_relations
      WHERE source_chunk_id = ANY(cast(:ids as text[]))
        AND relation_type = ANY(cast(:rts as text[])) AND is_stale = false
    UNION
    SELECT source_chunk_id AS n FROM chunk_relations
      WHERE target_chunk_id = ANY(cast(:ids as text[]))
        AND relation_type = ANY(cast(:rts as text[])) AND is_stale = false
""")

_RELATION_TYPE_MAP: dict[str, list[str]] = {
    "calls": [],                                   # 特判：call_graph 表
    "implements": ["CODE_IMPLEMENTS"],
    "extends": ["CODE_EXTENDS"],
    "doc_anchor": ["DOC_TO_CODE", "CODE_TO_DOC"],
}


def _edges_sql(relation_types: list[str] | None) -> list[tuple[object, dict[str, list[str]]]] | None:
    """relation_types=None → [(旧 _NEIGHBOR_SQL, {})]（逐字节现行为）；非空 → 按选择组装；空选集 → None。"""
    if relation_types is None:
        return [(_NEIGHBOR_SQL, {})]
    pairs: list[tuple[object, dict[str, list[str]]]] = []
    if "calls" in relation_types:
        pairs.append((_CALLS_SQL, {}))
    rts = sorted({rt for tok in relation_types if tok != "calls"
                  for rt in _RELATION_TYPE_MAP.get(tok, [])})
    if rts:
        pairs.append((_TYPED_SQL, {"rts": rts}))
    return pairs or None


async def fetch_chunks(session: AsyncSession, chunk_ids: list[str]) -> list[dict]:
    """按 chunk_id 从 code_chunks/doc_chunks 取内容/元数据（向量召回结果回填用）。"""
    if not chunk_ids:
        return []
    out: list[dict] = []
    code_sql = text("""SELECT chunk_id, content, class_name, method_name FROM code_chunks
                       WHERE chunk_id = ANY(cast(:ids as text[])) AND is_deleted=false""")
    for r in (await session.execute(code_sql, {"ids": chunk_ids})).mappings():
        out.append({"chunk_id": r["chunk_id"], "kind": "code", "content": r["content"],
                    "class_name": r["class_name"], "method_name": r["method_name"], "heading_path": None})
    doc_sql = text("""SELECT chunk_id, content, heading_path FROM doc_chunks
                      WHERE chunk_id = ANY(cast(:ids as text[])) AND is_deleted=false""")
    for r in (await session.execute(doc_sql, {"ids": chunk_ids})).mappings():
        out.append({"chunk_id": r["chunk_id"], "kind": "doc", "content": r["content"],
                    "class_name": None, "method_name": None, "heading_path": r["heading_path"] or []})
    return out


async def graph_recall(session: AsyncSession, seed_chunk_ids: list[str], *,
                       depth: int = 1, max_nodes: int = 12,
                       relation_types: list[str] | None = None) -> list[dict]:
    """BFS 扩展。relation_types=None（默认）= 旧行为（call_graph + chunk_relations 全边混合）；
    非空 = M32 关系类型过滤（tokens: calls/implements/extends/doc_anchor，未知 token 忽略）。"""
    if not seed_chunk_ids:
        return []
    sql_pairs = _edges_sql(relation_types)
    if not sql_pairs:
        return []
    seeds = set(seed_chunk_ids)
    visited = set(seeds)
    frontier = list(seeds)
    neighbors: set[str] = set()
    for _ in range(depth):
        if not frontier:
            break
        rows: list = []
        for sql, extra in sql_pairs:
            rows.extend((await session.execute(sql, {"ids": frontier, **extra})).all())
        nxt: list[str] = []
        for (nid,) in rows:
            if nid is None or nid in visited:
                continue
            visited.add(nid)
            neighbors.add(nid)
            nxt.append(nid)
            if len(neighbors) >= max_nodes:
                break
        frontier = nxt
    return await fetch_chunks(session, list(neighbors))
