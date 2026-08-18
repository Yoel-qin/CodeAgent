"""M42 Redis 缓存客户端：高频 QA 答案 + 查询 embedding（进程单例，lifespan 管理）。

软失败契约：Redis 不可达 / 命令异常 → miss 语义（get 返 None / set 静默）+ 单次 warning，
绝不影响请求（同「缺 API key 优雅降级）。``QA_CACHE_ENABLED`` off → 不初始化（get 返 None）。
键策略集中在此：归一化 → sha256；qa:/emb: 前缀隔离两个命名空间。
"""
from __future__ import annotations

import hashlib
import json
import logging

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

_cache: CacheClient | None = None


def normalize_query(q: str) -> str:
    """缓存键归一化：strip + lower + 空白折叠（精确匹配口径）。"""
    return " ".join((q or "").strip().lower().split())


def qa_cache_key(repo: str, normalized_query: str) -> str:
    return hashlib.sha256(f"{repo}|{normalized_query}".encode()).hexdigest()


def embed_cache_key(strategy: str, models: str, normalized_query: str) -> str:
    return hashlib.sha256(f"emb|{strategy}|{models}|{normalized_query}".encode()).hexdigest()


class CacheClient:
    """薄封装：JSON 序列化 + 前缀 + TTL + 软失败。构造注入 redis 实例（便于测试 fake）。"""

    def __init__(self, redis_client) -> None:
        self._r = redis_client

    async def qa_get(self, key: str) -> dict | None:
        try:
            raw = await self._r.get(f"qa:{key}")
            obj = json.loads(raw) if raw else None
            return obj if isinstance(obj, dict) else None
        except Exception as e:  # noqa: BLE001
            logger.warning("M42 qa_get failed (treated as miss): %s", e)
            return None

    async def qa_set(self, key: str, value: dict) -> None:
        try:
            await self._r.set(f"qa:{key}", json.dumps(value, ensure_ascii=False),
                              ex=settings.qa_cache_ttl_seconds)
        except Exception as e:  # noqa: BLE001
            logger.warning("M42 qa_set failed (skip caching): %s", e)

    async def embed_get(self, key: str) -> dict[str, list[float] | None] | None:
        """返回 query_embed 同形 dict；miss/损坏 → None。"""
        try:
            raw = await self._r.get(f"emb:{key}")
            obj = json.loads(raw) if raw else None
            return obj if isinstance(obj, dict) else None
        except Exception as e:  # noqa: BLE001
            logger.warning("M42 embed_get failed (treated as miss): %s", e)
            return None

    async def embed_set(self, key: str, vec: dict[str, list[float] | None]) -> None:
        try:
            await self._r.set(f"emb:{key}", json.dumps(vec),
                              ex=settings.embed_cache_ttl_seconds)
        except Exception as e:  # noqa: BLE001
            logger.warning("M42 embed_set failed (skip caching): %s", e)


async def init_cache_client() -> None:
    """lifespan 启动时调用：ping 通才启用；失败只 warning（缓存 = 纯优化，绝不为它炸启动）。"""
    global _cache
    if _cache is not None or not settings.qa_cache_enabled:
        return
    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=1.5,
                              decode_responses=True)
        await r.ping()
        _cache = CacheClient(r)
    except Exception as e:  # noqa: BLE001
        logger.warning("M42 cache client init failed (cache disabled): %s", e)
        _cache = None


def get_cache_client() -> CacheClient | None:
    return _cache


async def close_cache_client() -> None:
    global _cache
    if _cache is None:
        return
    try:
        await _cache._r.aclose()
    except Exception:  # noqa: BLE001
        pass
    _cache = None
