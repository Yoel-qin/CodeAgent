"""Task 6：评测 REST——POST /run 转调 service（monkeypatch）+ GET 列表/详情（真 PG 真清）。
TestClient lifespan 钉 load_tools noop + engine dispose（同 test_chat_api 模式）。

brief 适配（有据偏差，须带入评审）：brief 的 ``test_get_runs_and_detail`` 函数体首行
``from datetime import UTC, datetime`` 全函数未使用——ruff F401 会打红硬门
（``uv run ruff check .`` 净），删除该行，其余逐字。
"""
import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from app.agent import tools_loader
    from app.db.base import engine

    async def _noop_load(transports=None):
        return None

    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    yield
    slog = logging.getLogger("sqlalchemy")
    prev, slog.level = slog.level, logging.CRITICAL + 1
    try:
        asyncio.run(engine.dispose())
    finally:
        slog.setLevel(prev)


@pytest.fixture
def _cleanup_runs():
    from sqlalchemy import create_engine, text

    eng = create_engine(settings.postgres_dsn_sync)
    with eng.connect() as conn:
        before = {r[0] for r in conn.execute(text("select id from eval_runs where repo='test-api-eval'"))}
    yield
    with eng.connect() as conn:
        conn.execute(text("delete from eval_runs where repo='test-api-eval' and id <> any(:ids)"),
                     {"ids": list(before)})
        conn.commit()
    eng.dispose()


async def _fake_flow(**kw):
    return {"id": 7, "repo": "test-api-eval", "kind": "single", "status": "DONE",
            "config": None, "metrics": None, "per_query": None, "error": None,
            "created_at": None, "finished_at": None}


def test_post_run_delegates(monkeypatch):
    from app.main import app
    from app.services import eval_service

    captured = {}

    async def _flow(**kw):
        captured.update(kw)
        return await _fake_flow()

    monkeypatch.setattr(eval_service, "run_and_persist", _flow)
    with TestClient(app) as client:
        r = client.post("/v1/eval/run", json={"repo": "rocketmq", "judge": True})
        assert r.status_code == 200 and r.json()["id"] == 7
        assert captured["repo"] == "rocketmq" and captured["judge"] is True


def test_post_run_validation_422(monkeypatch):
    from app.main import app

    with TestClient(app) as client:
        # variant name 重复
        assert client.post("/v1/eval/run", json={"variants": [
            {"name": "a"}, {"name": "a"}]}).status_code == 422
        # rounds_code 越界
        assert client.post("/v1/eval/run", json={"variants": [
            {"rounds_code": 0}]}).status_code == 422
        assert client.post("/v1/eval/run", json={"variants": [
            {"rounds_code": 99}]}).status_code == 422


def test_get_runs_and_detail(_cleanup_runs):
    from sqlalchemy import create_engine, text

    from app.main import app

    eng = create_engine(settings.postgres_dsn_sync)
    with eng.connect() as conn:
        conn.execute(text(
            "insert into eval_runs (repo, kind, status, config) "
            "values ('test-api-eval', 'single', 'DONE', '{}'::jsonb) returning id"))
        conn.commit()
        with eng.connect() as conn:
            rid = conn.execute(text(
                "select id from eval_runs where repo='test-api-eval' "
                "order by id desc limit 1")).scalar_one()
    try:
        with TestClient(app) as client:
            lst = client.get("/v1/eval/runs").json()
            assert any(i["id"] == rid for i in lst["items"])
            assert client.get(f"/v1/eval/runs/{rid}").status_code == 200
            assert client.get("/v1/eval/runs/99999999").status_code == 404
    finally:
        eng.dispose()


def test_post_run_bad_golden_path_200_failed(_cleanup_runs):
    """I1：坏 golden_path → 200 + status=FAILED（不再 500）；repo 显式给可清理值
    （服务测试已断言 repo 缺席时的 settings 回落，此处只钉 API 软失败契约）。"""
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/v1/eval/run", json={"golden_path": "no-such.yaml",
                                              "repo": "test-api-eval"})
        assert r.status_code == 200 and r.json()["status"] == "FAILED"
        assert r.json()["error"]
