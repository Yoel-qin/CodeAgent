"""Embedding 连通性自检：验证 .env 的 EMBEDDING_API_KEY / BASE_URL / MODEL，并打印维度。

用法: uv run python scripts/embedding_ping.py
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.clients import embedding_client  # noqa: E402
from app.clients.embedding_client import embed_texts_sync  # noqa: E402
from app.core.config import settings  # noqa: E402


def main() -> int:
    print(f"provider : {settings.embedding_provider}")
    print(f"base_url : {settings.embedding_base_url}")
    print(f"model    : {settings.embedding_model}")
    masked = (settings.embedding_api_key[:6] + "..." + settings.embedding_api_key[-4:]) if settings.embedding_api_key else "(empty)"
    print(f"api_key  : {masked}")
    print(f"enabled  : {embedding_client.enabled()}")
    if not embedding_client.enabled():
        print("\n未配置 EMBEDDING_API_KEY（向量召回将跳过）。请在 .env 设置。")
        return 1
    try:
        vecs = embed_texts_sync(["事务消息回查机制", "checkLocalTransaction"], timeout=60)
        print(f"\nOK: 2 条文本 → 维度 {len(vecs[0])}（Milvus collection DIM=1024 需一致）")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"\n调用失败：{type(e).__name__}: {str(e)[:300]}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
