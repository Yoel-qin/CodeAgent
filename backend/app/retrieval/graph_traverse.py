"""图遍历召回（设计 §11.3 路径 D）：从种子节点沿 call_graph + chunk_relations BFS 扩展。"""
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
                       depth: int = 1, max_nodes: int = 12) -> list[dict]:
    if not seed_chunk_ids:
        return []
    seeds = set(seed_chunk_ids)
    visited = set(seeds)
    frontier = list(seeds)
    neighbors: set[str] = set()
    for _ in range(depth):
        if not frontier:
            break
        rows = (await session.execute(_NEIGHBOR_SQL, {"ids": frontier})).all()
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
