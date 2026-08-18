"""M42 cache_client 测试（_FakeRedis，零真实 Redis）。"""

import pytest

import app.clients.cache_client as cc
from app.clients.cache_client import (
    CacheClient,
    embed_cache_key,
    normalize_query,
    qa_cache_key,
)
from app.core.config import settings


class _FakeRedis:
    def __init__(self, fail: bool = False):
        self.store: dict = {}
        self.fail = fail

    async def ping(self):
        if self.fail:
            raise ConnectionError("down")
        return True

    async def get(self, k):
        if self.fail:
            raise ConnectionError("down")
        return self.store.get(k)

    async def set(self, k, v, ex=None):
        if self.fail:
            raise ConnectionError("down")
        self.store[k] = v
        self.store.setdefault("__ttl__", {})[k] = ex


@pytest.mark.asyncio
async def test_qa_roundtrip_and_prefix():
    r = _FakeRedis()
    c = CacheClient(r)
    await c.qa_set(qa_cache_key("repo", "q"), {"answer": "A"})
    assert await c.qa_get(qa_cache_key("repo", "q")) == {"answer": "A"}
    assert any(k.startswith("qa:") for k in r.store)


@pytest.mark.asyncio
async def test_embed_roundtrip_shape():
    r = _FakeRedis()
    c = CacheClient(r)
    vecs = {"unified": [0.1, 0.2], "code": None}
    await c.embed_set("k", vecs)
    assert await c.embed_get("k") == vecs
    assert await c.embed_get("missing") is None


@pytest.mark.asyncio
async def test_soft_fail_is_miss():
    """Redis 挂 → get None / set 静默，绝不抛。"""
    c = CacheClient(_FakeRedis(fail=True))
    assert await c.qa_get("k") is None
    assert await c.embed_get("k") is None
    await c.qa_set("k", {"a": 1})       # 不抛即通过
    await c.embed_set("k", {"unified": [1.0]})


@pytest.mark.asyncio
async def test_corrupt_json_is_miss():
    r = _FakeRedis()
    r.store["qa:k"] = "{not json"
    c = CacheClient(r)
    assert await c.qa_get("k") is None


def test_normalize_query():
    assert normalize_query("  Hello   WORLD ! ") == "hello world !"


def test_key_determinism_and_separation():
    assert qa_cache_key("r", "q") == qa_cache_key("r", "q")
    assert qa_cache_key("r1", "q") != qa_cache_key("r2", "q")
    assert embed_cache_key("unified", "m", "q") != qa_cache_key("unified", "q")


@pytest.mark.asyncio
async def test_init_disabled_and_failure(monkeypatch):
    monkeypatch.setattr(settings, "qa_cache_enabled", False)
    cc._cache = None
    await cc.init_cache_client()
    assert cc.get_cache_client() is None
    monkeypatch.setattr(settings, "qa_cache_enabled", True)
    monkeypatch.setattr(cc.aioredis, "from_url", lambda *a, **kw: _FakeRedis(fail=True))
    await cc.init_cache_client()          # 连不上：不抛、单例留 None
    assert cc.get_cache_client() is None
    monkeypatch.setattr(cc.aioredis, "from_url", lambda *a, **kw: _FakeRedis())
    await cc.init_cache_client()
    assert cc.get_cache_client() is not None
    await cc.close_cache_client()
    assert cc.get_cache_client() is None
