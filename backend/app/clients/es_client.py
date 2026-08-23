"""Elasticsearch 客户端：索引 + BM25 检索（设计 §11.3 路径 B）。

中文默认走 keywords 字段（入库前 jieba 预分词数组）terms 精确匹配，content（standard
分析器）覆盖英文/代码标识符。M31：``ES_IK_ENABLED=on`` 后索引重建为 IK mapping——
content=ik_max_word/ik_smart + content.code 子字段（word_delimiter_graph 拆 camelCase
保原词）+ chinese_comment 注释字段（检索期 boost 2.0），search 相应扩为 4 子句。
on 需先 ``scripts/install_es_plugins.py`` 装插件 + ``rebuild_es_index.py`` 重建；
旧索引 + on 开关：未映射子字段 match 返回 0 命中不报错，安全过渡。
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

# M31：IK mapping（ES_IK_ENABLED=on 时 ensure_index 采用；spec §3.3）。
# content.code 子字段拆 camelCase/snake_case 且 preserve_original（"producer" 可命中
# DefaultMQProducerImpl）；chinese_comment 走注释抽取（metadata.extract_chinese_comment）。
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
            "chunk_id": {"type": "keyword"},
            "kind": {"type": "keyword"},
            "content": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_smart",
                "fields": {"code": {"type": "text", "analyzer": "code_analyzer"}},
            },
            "chinese_comment": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_smart",
            },
            "keywords": {"type": "keyword"},  # jieba 预分词兜底保留（双分词体系并存）
            "class_name": {"type": "keyword"},
            "method_name": {"type": "keyword"},
            "heading_path": {"type": "keyword"},
            "file_path": {"type": "keyword"},
        },
    },
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
    if bool(es.indices.exists(index=INDEX)):
        return  # 已存在不重建（升级/回退走 scripts/rebuild_es_index.py）
    if settings.es_ik_enabled:
        # M31：on → IK mapping（插件未装时这里 400，被上层 try/except 软失败降级为空）
        es.indices.create(index=INDEX, settings=_MAPPING_IK["settings"],
                          mappings=_MAPPING_IK["mappings"])
    else:
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
        if settings.es_ik_enabled:
            # M31：content.code 拆 camelCase 子字段（"producer" 命中 DefaultMQProducerImpl）
            # + 中文注释字段高权重（javadoc 此前不进 ES，注释语义检索的落点）
            should.append({"match": {"content.code": {"query": raw_query, "boost": 1.0}}})
            should.append({"match": {"chinese_comment": {"query": raw_query, "boost": 2.0}}})
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
