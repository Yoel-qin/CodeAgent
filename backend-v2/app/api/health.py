"""/health：PG/Redis/Milvus/ES/LLM-config 五组件探活，各自 try/except 软失败。

阻塞型 client（pymilvus / elasticsearch-py）经 ``asyncio.to_thread(lambda: ...)``
避免事件循环卡死（注意 to_thread 不能传 kwargs，故用 lambda 闭包）。
"""
import asyncio

import redis as redis_lib
from elasticsearch import Elasticsearch
from fastapi import APIRouter
from loguru import logger
from pymilvus import MilvusClient
from sqlalchemy import text

from app.core.config import settings
from app.db.base import engine

router = APIRouter(tags=["health"])


async def ping_postgres() -> dict:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}


async def ping_redis() -> dict:
    r = redis_lib.asyncio.from_url(settings.redis_url)
    try:
        await r.ping()
        return {"status": "ok"}
    finally:
        await r.aclose()


async def ping_milvus() -> dict:
    client = MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}")
    return {"status": "ok", "collections": await asyncio.to_thread(lambda: client.list_collections())}


async def ping_es() -> dict:
    es = Elasticsearch(settings.es_url)
    return {"status": "ok", "version": (await asyncio.to_thread(lambda: es.info()))["version"]["number"]}


def _llm_status() -> dict:
    if settings.llm_api_key:
        return {"status": "ok", "model": settings.llm_model}
    return {"status": "unconfigured", "model": settings.llm_model}


@router.get("/health")
async def health() -> dict:
    components: dict[str, dict] = {}
    for name, fn in (
        ("postgres", ping_postgres),
        ("redis", ping_redis),
        ("milvus", ping_milvus),
        ("elasticsearch", ping_es),
    ):
        try:
            components[name] = await asyncio.wait_for(fn(), timeout=5)
        except Exception as e:  # noqa: BLE001 —— 探活失败只降级
            components[name] = {"status": "down", "error": f"{type(e).__name__}: {e}"}
            logger.warning(f"[health] {name} down: {e}")
    components["llm_config"] = _llm_status()
    ok = all(c.get("status") in ("ok", "unconfigured") for c in components.values())
    return {"status": "ok" if ok else "degraded", "components": components}
