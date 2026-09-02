"""M5 Task 12：POST /v1/sync/webhook（push 事件入队 Redis Stream）。

brief 2 个逐字 + 三处测试环境适配（断言逐行不动）：
1. autouse 钉 ``tools_loader.load_tools`` 为 noop——TestClient 会跑 lifespan，
   不钉则真连 127.0.0.1:8110/8111/8112 的 MCP server（与 test_chat_api 同款处理）。
2. autouse 前后 DEL 测试流 key（``v2:pipe:test:wh*``）——删除 stream key 连 consumer
   group 一起删掉，与 test_queue.py 的 rq fixture 同款清场。
3. test_webhook_unknown_repo_400 里 app/TestClient 两个 import 换行排序（ruff I001）；
   test_worker_a.py 的 brief 逐字分号行加 ``# noqa: E702``。
"""

import pytest

TEST_STREAM = "v2:pipe:test:wh"
TEST_DEAD = "v2:pipe:test:whd"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    import redis

    from app.agent import tools_loader
    from app.core.config import settings

    async def _noop_load(transports=None):
        return None

    # main.py 走模块属性调用 tools_loader.load_tools——钉此处即阻断 lifespan 真连 MCP
    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    r.delete(TEST_STREAM, TEST_DEAD)
    yield
    r.delete(TEST_STREAM, TEST_DEAD)


def test_webhook_enqueues_push(monkeypatch):
    from app.main import app
    from app.pipeline import queue as q_mod
    monkeypatch.setattr(q_mod.settings, "pipe_stream", "v2:pipe:test:wh")
    monkeypatch.setattr("app.api.sync.settings.repos_root", "D:/project/CodeRagAgent/backend-v2/tests/fixtures")
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        r = client.post("/v1/sync/webhook", json={
            "repo": "mini_repo", "commit_hash": "abc",
            "files": [{"path": "com/example/broker/CommitLog.java", "status": "M"}]})
        assert r.status_code == 200 and r.json()["enqueued"] is True
        from app.pipeline.queue import RedisStreamQueue
        q = RedisStreamQueue(stream="v2:pipe:test:wh", dead="v2:pipe:test:whd", group="test-wh")
        evs = q.consume(count=3, block_ms=500)
        assert evs and evs[0].kind == "push"


def test_webhook_unknown_repo_400(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setattr("app.api.sync.settings.repos_root", "D:/nonexistent")
    with TestClient(app) as client:
        assert client.post("/v1/sync/webhook",
                           json={"repo": "x",
                                 "files": [{"path": "a.java", "status": "A"}]}).status_code == 400
