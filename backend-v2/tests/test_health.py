"""GET /health：组件全 down 也只返回 degraded 200，绝不 5xx。

brief 适配（有据偏差，须带入评审）：autouse 测后 ``engine.dispose()`` 清 app 共享
engine 池——KEEP① 起 lifespan 会跑 eval_runs 孤儿回收（经 SessionLocal 触共享池），
池化 asyncpg 连接绑在 TestClient 的独立事件循环上，测后不 dispose 会在进程收尾 GC
时冒「Connection._cancel was never awaited」警告（test_documents_api 同款处理）。
"""
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _dispose_app_engine():
    """测后清 app 共享 engine 池（lifespan 回收经 SessionLocal 触共享池）。"""
    import asyncio

    from app.db.base import engine

    yield
    slog = logging.getLogger("sqlalchemy")
    prev, slog.level = slog.level, logging.CRITICAL + 1
    try:
        asyncio.run(engine.dispose())
    finally:
        slog.setLevel(prev)


def test_health_all_components_down_returns_degraded():
    with (
        patch("app.api.health.ping_postgres", new=AsyncMock(side_effect=RuntimeError("pg down"))),
        patch("app.api.health.ping_redis", new=AsyncMock(side_effect=RuntimeError("redis down"))),
        patch("app.api.health.ping_milvus", new=AsyncMock(side_effect=RuntimeError("milvus down"))),
        patch("app.api.health.ping_es", new=AsyncMock(side_effect=RuntimeError("es down"))),
    ):
        with TestClient(app) as client:
            resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    for name in ("postgres", "redis", "milvus", "elasticsearch"):
        assert body["components"][name]["status"] == "down"


def test_health_all_ok():
    with (
        patch("app.api.health.ping_postgres", new=AsyncMock(return_value={"status": "ok"})),
        patch("app.api.health.ping_redis", new=AsyncMock(return_value={"status": "ok"})),
        patch("app.api.health.ping_milvus", new=AsyncMock(return_value={"status": "ok"})),
        patch("app.api.health.ping_es", new=AsyncMock(return_value={"status": "ok"})),
    ):
        with TestClient(app) as client:
            resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
