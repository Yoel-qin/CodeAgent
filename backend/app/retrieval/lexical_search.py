"""词法召回（PG）：按 keywords 数组重叠 + content ILIKE 召回，关键词重叠数打分。

这是 BM25（Elasticsearch，Phase 3）到位前的占位实现，覆盖代码与文档两路。
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lexical_recall(session: AsyncSession, terms: list[str], *, top_k: int = 20,
                          allowed_kinds: set[str] | None = None) -> list[dict]:
    if not terms:
        return []
    term_set = {t.lower() for t in terms}

    results: list[dict] = []
    # 代码 chunk
    if allowed_kinds is None or "code" in allowed_kinds:
        code_sql = text("""
            SELECT chunk_id, content, class_name, method_name, keywords
            FROM code_chunks
            WHERE is_deleted = false AND keywords ?| cast(:terms as text[])
        """)
        for r in (await session.execute(code_sql, {"terms": list(term_set)})).mappings():
            kws = {k.lower() for k in (r["keywords"] or [])}
            results.append({
                "chunk_id": r["chunk_id"], "kind": "code", "content": r["content"],
                "class_name": r["class_name"], "method_name": r["method_name"],
                "heading_path": None, "score": float(len(kws & term_set)),
            })

    # 文档 chunk
    if allowed_kinds is None or "doc" in allowed_kinds:
        doc_sql = text("""
            SELECT chunk_id, content, heading_path, keywords
            FROM doc_chunks
            WHERE is_deleted = false AND keywords ?| cast(:terms as text[])
        """)
        for r in (await session.execute(doc_sql, {"terms": list(term_set)})).mappings():
            kws = {k.lower() for k in (r["keywords"] or [])}
            results.append({
                "chunk_id": r["chunk_id"], "kind": "doc", "content": r["content"],
                "class_name": None, "method_name": None,
                "heading_path": r["heading_path"] or [],
                "score": float(len(kws & term_set)),
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]
