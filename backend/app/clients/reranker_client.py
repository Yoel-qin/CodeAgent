"""重排客户端：OpenAI/SiliconFlow 兼容的 /rerank 端点（Cohere 风格）。

精排两阶段（设计 §11.5 粗排 / §11.6 精排）都走这里——同一端点不同 model。
RERANKER_API_KEY 留空时复用 EMBEDDING_API_KEY（同一供应商同一 Key）；
未配置 Key 或 RERANK_ENABLED=False 时 enabled()=False，上层管道优雅跳过精排。
"""
from __future__ import annotations

import httpx

from app.core.config import settings


def _key() -> str:
    """优先用专用 Key，否则复用嵌入 Key（硅基流动等同一账号通用）。"""
    return settings.reranker_api_key or settings.embedding_api_key


def enabled() -> bool:
    """重排是否就绪：总开关开 + 有可用 Key。"""
    return settings.rerank_enabled and bool(_key())


def _endpoint() -> str:
    return settings.reranker_base_url.rstrip("/") + "/rerank"


async def rerank(
    query: str, documents: list[str], *, model: str, top_n: int, timeout: float = 60.0,
) -> list[tuple[int, float]]:
    """调用 /rerank，返回 [(原始文档下标, 相关性分数)]，按分数降序。

    失败抛异常（HTTPError / 非预期响应），由检索管道 try/except 捕获后降级到 RRF 排序。
    """
    if not documents:
        return []
    payload = {
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": min(top_n, len(documents)),
        "return_documents": False,
    }
    headers = {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(_endpoint(), headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"rerank HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()

    results = data.get("results") or data.get("data") or []
    out: list[tuple[int, float]] = []
    for item in results:
        idx = item.get("index")
        score = item.get("relevance_score", item.get("score"))
        if idx is None or score is None:
            continue
        out.append((int(idx), float(score)))
    out.sort(key=lambda x: x[1], reverse=True)
    return out
