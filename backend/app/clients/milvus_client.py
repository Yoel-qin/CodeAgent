"""Milvus 客户端：向量入库 + ANN 检索（设计 §7.4 / §11.3 路径 A）。

Phase 1 用单一 collection `coderag_vectors`（BGE-M3 1024d）+ kind 过滤；
Phase 5 引入代码专用嵌入时再拆 code/doc collection。
"""
from __future__ import annotations

from pymilvus import DataType, MilvusClient

from app.core.config import settings

COLLECTION = "coderag_vectors"
DIM = 1024

_client: MilvusClient | None = None


def get_client() -> MilvusClient:
    global _client
    if _client is None:
        _client = MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}", timeout=15)
    return _client


def ensure_collection() -> None:
    c = get_client()
    if c.has_collection(COLLECTION):
        return
    schema = c.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=DIM)
    schema.add_field("kind", DataType.VARCHAR, max_length=16)
    idx = c.prepare_index_params()
    idx.add_index(field_name="embedding", index_type="HNSW", metric_type="COSINE",
                  params={"M": 32, "efConstruction": 256})
    c.create_collection(collection_name=COLLECTION, schema=schema, index_params=idx)


def upsert_vectors(rows: list[dict]) -> None:
    """rows: [{chunk_id, embedding, kind}]"""
    if not rows:
        return
    ensure_collection()
    get_client().upsert(collection_name=COLLECTION, data=rows)


def search(query_vec: list[float], top_k: int = 20, kind: str | None = None) -> list[dict]:
    ensure_collection()
    flt = f'kind == "{kind}"' if kind else ""
    res = get_client().search(
        collection_name=COLLECTION, data=[query_vec], anns_field="embedding",
        limit=top_k, filter=flt, output_fields=["kind"],
        search_params={"metric_type": "COSINE", "params": {"ef": 128}},
    )
    out: list[dict] = []
    for h in res[0]:
        ent = h.get("entity", {}) or {}
        out.append({"chunk_id": h.get("id"), "kind": ent.get("kind"), "score": float(h.get("distance", 0.0))})
    return out
