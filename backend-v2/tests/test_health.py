"""GET /health：组件全 down 也只返回 degraded 200，绝不 5xx。"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


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
