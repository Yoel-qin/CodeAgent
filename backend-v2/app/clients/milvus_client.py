"""Milvus 客户端：v2 单 collection（v2_doc_chunks，1024-d HNSW COSINE）。"""
from __future__ import annotations

from pymilvus import DataType, MilvusClient

from app.core.config import settings

_COLLECTION = "v2_doc_chunks"
_DIM = 1024

_client: MilvusClient | None = None


def _esc(v: str) -> str:
    """转义拼进 Milvus filter expr 的字符串值（防止注入）。"""
    return v.replace("\\", "\\\\").replace('"', '\\"')


def get_client() -> MilvusClient:
    global _client
    if _client is None:
        _client = MilvusClient(
            uri=f"http://{settings.milvus_host}:{settings.milvus_port}", timeout=15,
        )
    return _client


def ensure_collection() -> None:
    c = get_client()
    if c.has_collection(_COLLECTION):
        return
    schema = c.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=256)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=_DIM)
    schema.add_field("repo", DataType.VARCHAR, max_length=128)
    schema.add_field("doc_name", DataType.VARCHAR, max_length=256)
    schema.add_field("section", DataType.VARCHAR, max_length=512)
    schema.add_field("title", DataType.VARCHAR, max_length=512)
    schema.add_field("module", DataType.VARCHAR, max_length=128, nullable=True)
    schema.add_field("page", DataType.INT64)
    idx = c.prepare_index_params()
    idx.add_index(
        field_name="embedding",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    c.create_collection(collection_name=_COLLECTION, schema=schema, index_params=idx)


def upsert_sections(rows: list[dict]) -> None:
    """rows: [{id, embedding, repo, doc_name, section, title, module, page}]。"""
    if not rows:
        return
    ensure_collection()
    get_client().upsert(collection_name=_COLLECTION, data=rows)


def search_sections(
    query_vec: list[float],
    *,
    top_k: int = 10,
    repo: str,
    module: str | None = None,
) -> list[dict]:
    """ANN 检索，repo filter 必带。返回 [{section_id, doc_name, title, anchor, score, module}]。"""
    ensure_collection()
    flt = f'repo == "{_esc(repo)}"'
    if module is not None:
        flt += f' && module == "{_esc(module)}"'
    res = get_client().search(
        collection_name=_COLLECTION,
        data=[query_vec],
        anns_field="embedding",
        limit=top_k,
        filter=flt,
        output_fields=["doc_name", "title", "section", "module"],
        search_params={"metric_type": "COSINE", "params": {"ef": 128}},
    )
    out: list[dict] = []
    if not res or not res[0]:
        return out
    for h in res[0]:
        ent = h.get("entity", {}) or {}
        out.append({
            "section_id": h.get("id"),
            "doc_name": ent.get("doc_name"),
            "title": ent.get("title"),
            "anchor": ent.get("section"),
            "score": float(h.get("distance", 0.0)),
            "module": ent.get("module"),
        })
    return out
