"""Elasticsearch 客户端：v2 单 index（v2_doc_sections），BM25 检索 + repo 过滤。"""
from __future__ import annotations

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

from app.core.config import settings

_INDEX = "v2_doc_sections"

_MAPPING = {
    "properties": {
        "section_id": {"type": "keyword"},
        "repo": {"type": "keyword"},
        "doc_name": {"type": "keyword"},
        "title": {"type": "text", "analyzer": "standard"},
        "anchor": {"type": "keyword"},
        "module": {"type": "keyword"},
        "content": {"type": "text", "analyzer": "standard"},
    }
}

# IK 分词映射（es_ik_enabled on 时 ensure_index 采用，沿旧库 _MAPPING_IK 移植）
_MAPPING_IK = {
    "settings": {
        "analysis": {
            "filter": {
                "code_split": {
                    "type": "word_delimiter_graph",
                    "split_on_case_change": True,
                    "preserve_original": True,
                },
            },
            "analyzer": {
                "code_analyzer": {
                    "tokenizer": "standard",
                    "filter": ["code_split", "lowercase"],
                },
            },
        },
    },
    "mappings": {
        "properties": {
            "section_id": {"type": "keyword"},
            "repo": {"type": "keyword"},
            "doc_name": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_smart",
            },
            "anchor": {"type": "keyword"},
            "module": {"type": "keyword"},
            "content": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_smart",
            },
        },
    },
}

_es: Elasticsearch | None = None


def get_es() -> Elasticsearch:
    global _es
    if _es is None:
        _es = Elasticsearch(settings.es_url, request_timeout=15)
    return _es


def ensure_index() -> None:
    es = get_es()
    if es.indices.exists(index=_INDEX):
        return
    if settings.es_ik_enabled:
        es.indices.create(
            index=_INDEX,
            settings=_MAPPING_IK["settings"],
            mappings=_MAPPING_IK["mappings"],
        )
    else:
        es.indices.create(index=_INDEX, mappings=_MAPPING)


def bulk_index_sections(docs: list[dict]) -> int:
    """批量索引文档段落。ES 不可用时静默返回 0（软失败）。"""
    if not docs:
        return 0
    try:
        ensure_index()
        actions = [
            {"_index": _INDEX, "_id": d["section_id"], "_source": d} for d in docs
        ]
        succ, _ = bulk(get_es(), actions, refresh=True)
        return succ
    except Exception:
        return 0


def search_sections(
    query: str, *, top_k: int = 10, repo: str
) -> list[dict]:
    """BM25 召回，repo filter 必带。返回 [{section_id, score, ...metadata}]。"""
    q = query.strip()
    if not q:
        return []
    try:
        ensure_index()
    except Exception:
        pass
    bool_q: dict = {
        "should": [{"match": {"content": {"query": q}}}],
        "minimum_should_match": 1,
        "filter": [{"term": {"repo": repo}}],
    }
    body = {"query": {"bool": bool_q}, "size": top_k}
    try:
        resp = get_es().search(index=_INDEX, **body)
    except Exception:
        return []
    out: list[dict] = []
    for hit in resp["hits"]["hits"]:
        s = hit["_source"]
        out.append({
            "section_id": s.get("section_id"),
            "score": float(hit["_score"] or 0.0),
            "doc_name": s.get("doc_name"),
            "title": s.get("title"),
            "anchor": s.get("anchor"),
            "module": s.get("module"),
            "repo": s.get("repo"),
        })
    return out
