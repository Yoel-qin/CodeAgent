"""文档三路检索（semantic / keyword / hybrid）+ 两个只读 PG 查询。

所有函数同步，外部异常返回 {"error": ...} 不抛；检索路软失败空形。
"""
from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.clients.embedding_client import embed_texts
from app.clients.es_client import search_sections as es_search_sections
from app.clients.milvus_client import search_sections as vector_search_sections
from app.core.config import settings
from app.core.fusion import rrf_fuse
from app.db.models.doc import DocSection, Document

# ── PG 模块级惰性单例（同步） ──────────────────────────────────────────────
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.postgres_dsn_sync)
    return _engine


def _pg_session() -> Session:
    return Session(bind=_get_engine().connect())


# ── 检索三路 ────────────────────────────────────────────────────────────────


def semantic_search(
    repo: str, query: str, top_k: int = 8, module: str | None = None
) -> dict:
    """向量语义检索。无嵌入 key → {"results": [], "recall": 0}。"""
    try:
        vecs = embed_texts([query])
    except Exception:
        return {"results": [], "recall": 0}
    if not vecs or not vecs[0]:
        return {"results": [], "recall": 0}
    try:
        hits = vector_search_sections(vecs[0], top_k=top_k, repo=repo, module=module)
    except Exception:
        hits = []
    return {"results": hits, "recall": len(hits)}


def keyword_search(repo: str, query: str, top_k: int = 8) -> dict:
    """BM25 关键词检索。ES 不可用 → 同空形（软失败）。"""
    try:
        hits = es_search_sections(query, top_k=top_k, repo=repo)
    except Exception:
        hits = []
    return {"results": hits, "recall": len(hits)}


def hybrid_search(
    repo: str, query: str, top_k: int = 8, module: str | None = None
) -> dict:
    """两路各取 top 20 → RRF 融合（vector 1.0, bm25 0.8）→ 截 top_k。"""
    # vector path
    try:
        vecs = embed_texts([query])
    except Exception:
        vecs = []
    vec_hits = []
    if vecs and vecs[0]:
        try:
            vec_hits = vector_search_sections(
                vecs[0], top_k=20, repo=repo, module=module
            )
        except Exception:
            vec_hits = []

    # bm25 path
    try:
        bm25_hits = es_search_sections(query, top_k=20, repo=repo)
    except Exception:
        bm25_hits = []

    fused = rrf_fuse(
        {"vector": vec_hits, "bm25": bm25_hits},
        weights={"vector": 1.0, "bm25": 0.8},
    )
    return {"results": fused[:top_k], "recall": len(fused)}


# ── PG 只读查询 ─────────────────────────────────────────────────────────────


def read_doc_section(repo: str, doc_id: int, anchor: str) -> dict:
    """读取指定文档段落。未命中 → {"error": "section not found"}。"""
    try:
        sess = _pg_session()
        try:
            section = (
                sess.execute(
                    select(DocSection, Document.doc_name)
                    .join(Document, DocSection.document_id == Document.id)
                    .where(
                        DocSection.document_id == doc_id,
                        DocSection.anchor == anchor,
                    )
                )
                .first()
            )
            if not section:
                return {"error": "section not found"}
            sec, doc_name = section
            return {
                "document_id": sec.document_id,
                "doc_name": doc_name,
                "anchor": sec.anchor,
                "title": sec.title,
                "content": sec.content,
                "kind": sec.kind,
            }
        finally:
            sess.close()
    except Exception as e:
        return {"error": str(e)}


def get_doc_toc(repo: str, doc_id: int | None = None) -> dict:
    """文档目录树。无 doc_id → 全 repo 文档树。"""
    try:
        sess = _pg_session()
        try:
            stmt = (
                select(DocSection, Document.doc_name)
                .join(Document, DocSection.document_id == Document.id)
                .order_by(DocSection.document_id, DocSection.order_index)
            )
            if doc_id is not None:
                stmt = stmt.where(DocSection.document_id == doc_id)
            rows = sess.execute(stmt).all()
            return {
                "toc": [
                    {
                        "document_id": sec.document_id,
                        "doc_name": doc_name,
                        "anchor": sec.anchor,
                        "title": sec.title,
                        "level": sec.level,
                        "order_index": sec.order_index,
                    }
                    for sec, doc_name in rows
                ]
            }
        finally:
            sess.close()
    except Exception as e:
        return {"error": str(e)}
