"""交叉链接召回（M32 ③ 第 5 路）：沿 chunk_relations 的 DOC↔CODE 锚点边双向扩展。

与图路（path D，调用边为主）正交：种子取 vector+lexical 双 kind 命中，扩展出的
对侧 chunk 按「被多少个种子链接」打分（多锚共识优先；同分按 chunk_id 稳定排序）。
is_stale 的锚点不扩展；种子自身不计入结果。整体在 pipeline 调用侧 try/except 软失败。
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.graph_traverse import fetch_chunks

_CROSSLINK_SQL = text("""
    SELECT target_chunk_id AS n FROM chunk_relations
      WHERE source_chunk_id = ANY(cast(:ids as text[]))
        AND relation_type = ANY(cast(:rts as text[])) AND is_stale = false
    UNION ALL
    SELECT source_chunk_id AS n FROM chunk_relations
      WHERE target_chunk_id = ANY(cast(:ids as text[]))
        AND relation_type = ANY(cast(:rts as text[])) AND is_stale = false
""")
_CROSSLINK_TYPES = ["DOC_TO_CODE", "CODE_TO_DOC"]


async def crosslink_recall(session: AsyncSession, seed_chunk_ids: list[str], *,
                           top_k: int = 20,
                           allowed_kinds: set[str] | None = None) -> list[dict]:
    if not seed_chunk_ids:
        return []
    seeds = set(seed_chunk_ids)
    rows = (await session.execute(
        _CROSSLINK_SQL, {"ids": list(seeds), "rts": _CROSSLINK_TYPES})).all()
    links: dict[str, int] = {}
    for (nid,) in rows:
        if nid is None or nid in seeds:
            continue
        links[nid] = links.get(nid, 0) + 1
    ranked = sorted(links.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
    chunks = await fetch_chunks(session, [cid for cid, _ in ranked])
    if allowed_kinds is not None:
        chunks = [c for c in chunks if c.get("kind") in allowed_kinds]
    return chunks
