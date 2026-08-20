"""Milvus 客户端：向量入库 + ANN 检索（设计 §7.4 / §11.3 路径 A）。

按 embedding_strategy 支持两种 collection 布局：
  unified → 单一 `coderag_vectors`（1024d，带 kind 字段 + kind 过滤）——框架一（全 BGE-M3）。
  dual    → `code_vectors`(768d, CodeBERT) + `doc_vectors`(1024d, BGE-M3)
            ——框架二（方案一）；collection 名即 kind，无 kind 字段。
            + `code_vectors_bge`(1024d, BGE-M3)：M25 代码的 BGE-M3 镜像索引（伪 kind "code_bge"），
              让多语言的 BGE-M3 也能检索代码（修 dual 向量对中文 NL 代码查询召回弱——CodeBERT 无中文，
              把中文 NL 查询嵌入稀疏区 → 漏召；BGE-M3 此前只搜 doc_vectors 看不到代码）。
HNSW COSINE；chunk_id VARCHAR 主键。
"""
from __future__ import annotations

from pymilvus import DataType, MilvusClient

from app.core.config import settings

UNIFIED_COLLECTION = "coderag_vectors"
UNIFIED_DIM = 1024
CODE_COLLECTION = "code_vectors"
CODE_DIM = 768           # CodeBERT
DOC_COLLECTION = "doc_vectors"
DOC_DIM = 1024           # BGE-M3
# M25：dual 模式代码的 BGE-M3 镜像索引（伪 kind "code_bge"）。code 的 code_* chunk_id 与
# code_vectors 相同，但向量来自 BGE-M3(1024d)，查询侧用 BGE-M3 查询向量检索，让 BGE-M3 也能找回代码。
CODE_BGE_COLLECTION = "code_vectors_bge"
CODE_BGE_DIM = 1024      # BGE-M3

_client: MilvusClient | None = None


def get_client() -> MilvusClient:
    global _client
    if _client is None:
        _client = MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}", timeout=15)
    return _client


def collection_for(strategy: str | None, kind: str | None) -> tuple[str, int, bool]:
    """返回 (collection 名, 维度, 是否带 kind 字段)。
    unified → 单 collection + kind 字段；dual → code(768d)/code_bge(1024d BGE 镜像)/doc 各一 collection，无 kind 字段。
    """
    s = strategy if strategy is not None else settings.embedding_strategy
    if s == "dual":
        if kind == "code":
            return CODE_COLLECTION, CODE_DIM, False
        if kind == "code_bge":
            return CODE_BGE_COLLECTION, CODE_BGE_DIM, False  # M25 BGE-M3 代码镜像
        return DOC_COLLECTION, DOC_DIM, False  # doc 或缺省
    return UNIFIED_COLLECTION, UNIFIED_DIM, True


def ensure_collection(strategy: str | None = None, kind: str | None = None) -> None:
    name, dim, has_kind = collection_for(strategy, kind)
    c = get_client()
    if c.has_collection(name):
        return
    schema = c.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
    if has_kind:
        schema.add_field("kind", DataType.VARCHAR, max_length=16)
    idx = c.prepare_index_params()
    idx.add_index(field_name="embedding", index_type="HNSW", metric_type="COSINE",
                  params={"M": 32, "efConstruction": 256})
    c.create_collection(collection_name=name, schema=schema, index_params=idx)


def upsert_vectors(strategy: str | None, kind: str, rows: list[dict]) -> None:
    """rows: [{chunk_id, embedding}]；unified 自动补 kind 字段，dual 按 collection 名写入。"""
    if not rows:
        return
    name, _, has_kind = collection_for(strategy, kind)
    ensure_collection(strategy, kind)
    data = [{**r, "kind": kind} for r in rows] if has_kind else rows
    get_client().upsert(collection_name=name, data=data)


def delete_vectors(strategy: str | None, kind: str, chunk_ids: list[str]) -> int:
    """按 chunk_id（Milvus VARCHAR 主键）硬删除向量，返回请求删除的条数。

    unified 单 collection 内 code/doc 的 chunk_id 全局唯一（code_/doc_ 前缀），按 PK 删即可；
    dual 需按 kind 选 collection。空列表为 no-op（不触碰客户端）。
    """
    if not chunk_ids:
        return 0
    name, _, _ = collection_for(strategy, kind)
    ensure_collection(strategy, kind)
    get_client().delete(collection_name=name, ids=list(chunk_ids))
    return len(chunk_ids)


def search(strategy: str | None, kind: str | None, query_vec: list[float],
           top_k: int = 20, allowed_kinds: list[str] | None = None) -> list[dict]:
    """ANN 检索。unified + kind=None → 不加 kind 过滤（混检 code+doc）。
    M45：allowed_kinds 非 None → unified 加 ``kind in [...]`` 过滤（dual 靠 collection 隔离，
    code 权限被拒时由 vector_search 直接跳过 code collection，不走到这里）。
    返回 [{chunk_id, kind, score}]（dual 的 kind 由入参/collection 名回填）。
    """
    name, _, has_kind = collection_for(strategy, kind)
    ensure_collection(strategy, kind)
    if has_kind and kind:
        flt = f'kind == "{kind}"'
    elif has_kind and allowed_kinds:
        flt = 'kind in [' + ", ".join(f'"{k}"' for k in allowed_kinds) + ']'
    else:
        flt = ""
    out_fields = ["kind"] if has_kind else []
    res = get_client().search(
        collection_name=name, data=[query_vec], anns_field="embedding",
        limit=top_k, filter=flt, output_fields=out_fields,
        search_params={"metric_type": "COSINE", "params": {"ef": 128}},
    )
    out: list[dict] = []
    for h in res[0]:
        ent = h.get("entity", {}) or {}
        out.append({
            "chunk_id": h.get("id"),
            "kind": ent.get("kind") if has_kind else kind,
            "score": float(h.get("distance", 0.0)),
        })
    return out
