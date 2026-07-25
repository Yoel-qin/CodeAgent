"""嵌入客户端：OpenAI 兼容的 /embeddings API（默认硅基流动 BGEI/bge-m3，1024d）。

embedding_provider=api 且配置 embedding_api_key 时启用；local 模式走 model_server（预留）。
向量维度必须与 Milvus collection DIM(1024) 一致——BGE-M3=1024。
"""
from __future__ import annotations

import httpx

from app.core.config import settings


def enabled() -> bool:
    """嵌入是否就绪：API 模式需配置 key（local/model_server 模式见 TODO）。"""
    return settings.embedding_provider.lower() == "api" and bool(settings.embedding_api_key)


def _endpoint() -> str:
    return settings.embedding_base_url.rstrip("/") + "/embeddings"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.embedding_api_key}"}


def embed_texts_sync(texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
    """同步嵌入（入库脚本用）。"""
    r = httpx.post(_endpoint(), headers=_headers(),
                   json={"model": settings.embedding_model, "input": texts}, timeout=timeout)
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


async def embed_texts(texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
    """异步嵌入（检索管道用）。"""
    async with httpx.AsyncClient() as c:
        r = await c.post(_endpoint(), headers=_headers(),
                         json={"model": settings.embedding_model, "input": texts}, timeout=timeout)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]
