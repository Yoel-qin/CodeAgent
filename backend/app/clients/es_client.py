"""Elasticsearch 客户端：索引 + BM25 检索（设计 §11.3 路径 B）。

中文无需 IK 分词器——keywords 字段在入库前已用 jieba 预分词（数组），
检索时用 terms 精确匹配；content 字段（standard 分析器）覆盖英文/代码标识符。
"""
from __future__ import annotations

from typing import Any

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

from app.core.config import settings

INDEX = "coderag_chunks"

_MAPPING = {
    "properties": {
        "chunk_id": {"type": "keyword"},
        "kind": {"type": "keyword"},
        "content": {"type": "text", "analyzer": "standard"},
        "keywords": {"type": "keyword"},  # 预分词数组；terms 精确匹配
        "class_name": {"type": "keyword"},
        "method_name": {"type": "keyword"},
        "heading_path": {"type": "keyword"},
        "file_path": {"type": "keyword"},
    }
}

_es: Elasticsearch | None = None


def get_es() -> Elasticsearch:
    global _es
    if _es is None:
        _es = Elasticsearch(settings.es_url, request_timeout=15)
    return _es


def ping() -> bool:
    try:
        return bool(get_es().ping())
    except Exception:
        return False


def ensure_index() -> None:
    es = get_es()
    if not bool(es.indices.exists(index=INDEX)):
        es.indices.create(index=INDEX, mappings=_MAPPING)


def delete_by_file(file_path: str) -> None:
    ensure_index()
    get_es().delete_by_query(index=INDEX, query={"term": {"file_path": file_path}},
                             refresh=True, ignore_unavailable=True)


def bulk_index_chunks(docs: list[dict]) -> int:
    if not docs:
        return 0
    ensure_index()
    actions = [{"_index": INDEX, "_id": d["chunk_id"], "_source": d} for d in docs]
    succ, _ = bulk(get_es(), actions, refresh=True)
    return succ


def index_chunks_safe(file_path: str, docs: list[dict]) -> None:
    """入库时同步索引到 ES；ES 不可用时静默跳过（不阻断 PG 入库）。"""
    try:
        delete_by_file(file_path)
        bulk_index_chunks(docs)
    except Exception:
        pass


def search(query_terms: list[str], raw_query: str, top_k: int = 20,
           kinds: list[str] | None = None) -> list[dict]:
    """BM25 召回：terms 命中 keywords（中文）+ match 命中 content（英文/代码）。
    M45：kinds 非 None → 加 kind terms 过滤（RBAC 检索过滤）。"""
    ensure_index()
    should: list[dict[str, Any]] = []
    if query_terms:
        should.append({"terms": {"keywords": query_terms, "boost": 2.0}})
    if raw_query.strip():
        should.append({"match": {"content": {"query": raw_query, "boost": 1.0}}})
    if not should:
        return []
    bool_q: dict[str, Any] = {"should": should, "minimum_should_match": 1}
    if kinds:
        bool_q["filter"] = [{"terms": {"kind": kinds}}]
    body = {"query": {"bool": bool_q}, "size": top_k}
    resp = get_es().search(index=INDEX, **body)
    out: list[dict] = []
    for hit in resp["hits"]["hits"]:
        s = hit["_source"]
        out.append({
            "chunk_id": s.get("chunk_id"),
            "kind": s.get("kind"),
            "content": s.get("content", ""),
            "class_name": s.get("class_name"),
            "method_name": s.get("method_name"),
            "heading_path": s.get("heading_path") or [],
            "score": float(hit["_score"] or 0.0),
        })
    return out
