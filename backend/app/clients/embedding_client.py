"""嵌入客户端：可切换双框架（见 docs/嵌入向量方案.md）。

- 框架一 / 文档侧（unified）：OpenAI 兼容 /embeddings API（默认硅基流动 BGE-M3，1024d）。
- 框架二 / 代码侧（dual）：本地 model_server 加载 CodeBERT（768d）。

统一调度（被 ingest / vector_search 调用，按 settings.embedding_strategy 分派）：
  ingest_embed(kind, texts)   同步，入库用
  query_embed(query)          异步，检索用，返回 {collection_role: vec|None}

各编码器的「不可用」（无 Key / model_server 未起 / 网络失败）统一降级为对应键 None，
vector_search 跳过该路，主链路不中断。
"""
from __future__ import annotations

import httpx

from app.core.config import settings

# ---------------------------------------------------------------------------
# 框架一 / 文档侧：BGE-M3 OpenAI 兼容 API（1024d）
# ---------------------------------------------------------------------------

def enabled() -> bool:
    """文档侧（BGE-M3 API）是否就绪：API 模式 + 已配置 key。"""
    return settings.embedding_provider.lower() == "api" and bool(settings.embedding_api_key)


def _endpoint() -> str:
    return settings.embedding_base_url.rstrip("/") + "/embeddings"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.embedding_api_key}"}


def embed_doc_texts_sync(texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
    """文档侧同步嵌入（入库脚本用）。"""
    r = httpx.post(_endpoint(), headers=_headers(),
                   json={"model": settings.embedding_model, "input": texts}, timeout=timeout)
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


async def embed_doc_texts(texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
    """文档侧异步嵌入（检索管道用）。"""
    async with httpx.AsyncClient() as c:
        r = await c.post(_endpoint(), headers=_headers(),
                         json={"model": settings.embedding_model, "input": texts}, timeout=timeout)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


# 向后兼容别名（= 文档侧 BGE-M3）
embed_texts_sync = embed_doc_texts_sync
embed_texts = embed_doc_texts


# ---------------------------------------------------------------------------
# 框架二 / 代码侧：CodeBERT 经本地 model_server（768d）
# ---------------------------------------------------------------------------

def code_enabled() -> bool:
    """代码侧（CodeBERT）是否启用：dual 策略 + 配置开关（实际可达性由调用方 try/except 兜底）。"""
    return settings.embedding_strategy == "dual" and settings.code_embedding_enabled


def _model_server_endpoint() -> str:
    return settings.model_server_url.rstrip("/") + "/embeddings"


def embed_code_sync(texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
    """代码侧同步嵌入：调 model_server 的 /embeddings（{texts} -> {embeddings, dim}）。"""
    if not texts:
        return []
    r = httpx.post(_model_server_endpoint(), json={"texts": texts}, timeout=timeout)
    r.raise_for_status()
    return r.json()["embeddings"]


async def embed_code(texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
    """代码侧异步嵌入（检索管道用）。"""
    if not texts:
        return []
    async with httpx.AsyncClient() as c:
        r = await c.post(_model_server_endpoint(), json={"texts": texts}, timeout=timeout)
        r.raise_for_status()
        return r.json()["embeddings"]


# ---------------------------------------------------------------------------
# 统一调度：按 embedding_strategy 分派
# ---------------------------------------------------------------------------

def strategy() -> str:
    return settings.embedding_strategy


def ingest_embed(kind: str, texts: list[str]) -> list[list[float]]:
    """入库嵌入（同步）。
    unified → 一律 BGE-M3 API；
    dual    → code 用 CodeBERT(model_server)，doc 用 BGE-M3 API；
            → "code_bge"（M25 代码的 BGE-M3 镜像，1024d）落 BGE-M3 分支（dispatch 仅对 kind=="code"
              严格走 CodeBERT，"code_bge" 走默认 BGE）。无需特殊处理——collection 由 milvus_client 按 kind 选。
    """
    if settings.embedding_strategy == "dual" and kind == "code":
        return embed_code_sync(texts)
    return embed_doc_texts_sync(texts)


async def query_embed(query: str) -> dict[str, list[float] | None]:
    """查询嵌入（异步），返回 {collection_role: vec|None}。
    unified → {"unified": bge_vec|None}（单 collection，kind 过滤）
    dual    → {"code": codebert_vec|None, "doc": bge_vec|None}
    任一编码器不可用对应值为 None（vector_search 跳过该路）。
    M42：QA_CACHE_ENABLED 时精确匹配缓存（键含 strategy+模型名+归一化文本）；
    全 None 结果不缓存（编码器不可用是瞬时态，不落 24h TTL）。
    """
    from app.clients.cache_client import embed_cache_key, get_cache_client, normalize_query
    cc = get_cache_client()
    key = None
    if cc is not None:
        models = settings.code_embedding_model if settings.embedding_strategy == "dual" else ""
        key = embed_cache_key(settings.embedding_strategy, models, normalize_query(query))
        cached = await cc.embed_get(key)
        if cached is not None:
            return cached

    async def _run() -> dict[str, list[float] | None]:
        if settings.embedding_strategy == "dual":
            code_vec: list[float] | None = None
            doc_vec: list[float] | None = None
            if code_enabled():
                try:
                    code_vec = (await embed_code([query]))[0]
                except Exception:
                    code_vec = None
            if enabled():
                try:
                    doc_vec = (await embed_doc_texts([query]))[0]
                except Exception:
                    doc_vec = None
            return {"code": code_vec, "doc": doc_vec}
        uni_vec: list[float] | None = None
        if enabled():
            try:
                uni_vec = (await embed_doc_texts([query]))[0]
            except Exception:
                uni_vec = None
        return {"unified": uni_vec}

    vecs = await _run()
    if cc is not None and key is not None and any(v is not None for v in vecs.values()):
        await cc.embed_set(key, vecs)
    return vecs
