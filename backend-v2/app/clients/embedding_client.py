"""Embedding 客户端：SiliconFlow BGE-M3 /embeddings（1024-d）。"""
from __future__ import annotations

import httpx

from app.core.config import settings

_BATCH_SIZE = 16


def embed_texts(texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
    """批量嵌入，每批 ≤16 条。空 key / 任何异常 → 返回 []。"""
    if not texts or not settings.embedding_api_key:
        return []
    url = settings.embedding_base_url.rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {settings.embedding_api_key}"}
    results: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        try:
            r = httpx.post(
                url,
                headers=headers,
                json={"model": settings.embedding_model, "input": batch},
                timeout=timeout,
            )
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            results.extend(d["embedding"] for d in data)
        except Exception:
            return []
    return results
