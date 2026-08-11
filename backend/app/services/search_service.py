"""全局关键词搜索服务（⌘K 用）：规则分词 → lexical_recall（PG 关键词重叠）→ snippet/label 组装。

刻意关键词级、纯 PG、零 API key —— 按键场景要快；语义/向量检索仍走 ``/v1/chat``。
``lexical_recall`` 复用 BM25 占位的 PG 实现（keywords JSONB 重叠打分），覆盖 code+doc 两路。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.lexical_search import lexical_recall
from app.retrieval.query_understanding import extract_query_terms

_SNIPPET_WIDTH = 160


def _label(row: dict) -> str:
    """命中的显示标签：code → 类.方法；doc → 章节面包屑（回退 content 头）。"""
    if row.get("kind") == "code":
        cls = row.get("class_name")
        meth = row.get("method_name")
        if cls and meth:
            return f"{cls}.{meth}"
        if cls:
            return str(cls)
        return str(row.get("chunk_id", "?"))
    hp = row.get("heading_path") or []
    if hp:
        return " › ".join(str(h) for h in hp)
    return (row.get("content") or "").strip()[:40] or str(row.get("chunk_id", "?"))


def _snippet(content: str | None, terms: list[str], width: int = _SNIPPET_WIDTH) -> str:
    """围绕首个命中的 term 截取预览片段（折叠空白，首尾按需加省略号）。无命中则取头部。"""
    text = (content or "").strip().replace("\n", " ")
    if not text:
        return ""
    low = text.lower()
    pos = -1
    for t in terms:
        i = low.find(t.lower())
        if i >= 0 and (pos < 0 or i < pos):
            pos = i
    if pos < 0:
        pos = 0
    half = width // 2
    start = max(0, pos - half)
    end = min(len(text), start + width)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


async def search(
    session: AsyncSession, q: str, *, kind: str | None = None, top_k: int = 12,
) -> dict:
    """关键词搜索 code+doc chunk。

    返回 ``{"q", "total", "items": [{chunk_id, kind, label, snippet, score}]}``。
    ``kind`` 为 ``"code"``/``"doc"`` 时仅留对应路；``top_k`` 控制最终返回条数（召回取
    2× 宽进严出，给过滤留余量）。
    """
    terms = extract_query_terms(q)
    rows = await lexical_recall(session, terms, top_k=max(top_k * 2, top_k))
    if kind in ("code", "doc"):
        rows = [r for r in rows if r.get("kind") == kind]
    items = [
        {
            "chunk_id": r.get("chunk_id"),
            "kind": r.get("kind"),
            "label": _label(r),
            "snippet": _snippet(r.get("content"), terms),
            "score": float(r.get("score") or 0.0),
        }
        for r in rows[:top_k]
    ]
    return {"q": q, "total": len(items), "items": items}
