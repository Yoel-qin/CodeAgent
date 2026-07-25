"""健康检查：探测各基础组件连通性（无重依赖，TCP/异步探测）。"""
from __future__ import annotations

import asyncio
import socket
from urllib.parse import urlparse

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.db import AsyncSessionLocal

router = APIRouter(tags=["health"])


async def _check_tcp(host: str, port: int, timeout: float = 1.5) -> bool:
    def _probe() -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    return await asyncio.to_thread(_probe)


async def _check_pg() -> bool:
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _check_redis() -> bool:
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=1.5)
        ok = await r.ping()
        await r.aclose()
        return bool(ok)
    except Exception:
        return False


@router.get("/health")
async def health() -> dict:
    """聚合健康状态。"""
    milvus_host = settings.milvus_host
    es_host = urlparse(settings.es_url).hostname or "localhost"
    minio_host = settings.minio_endpoint.split(":")[0]

    pg, redis_, milvus, es, minio = await asyncio.gather(
        _check_pg(),
        _check_redis(),
        _check_tcp(milvus_host, settings.milvus_port),
        _check_tcp(es_host, 9200),
        _check_tcp(minio_host, int(settings.minio_endpoint.split(":")[-1]) if ":" in settings.minio_endpoint else 9000),
    )

    components = {
        "postgres": pg,
        "redis": redis_,
        "milvus": milvus,
        "elasticsearch": es,
        "minio": minio,
    }
    overall = all(components.values())
    return {
        "status": "healthy" if overall else "degraded",
        "app": settings.app_name,
        "env": settings.app_env,
        "components": components,
    }
