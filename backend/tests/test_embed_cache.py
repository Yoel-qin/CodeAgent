"""M42 查询 embedding 缓存测试（unified 路径，fake httpx 层之上的 embed_doc_texts spy）。"""
from unittest.mock import AsyncMock

import pytest

import app.clients.embedding_client as ec
from app.clients.cache_client import CacheClient
from app.core.config import settings


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}

    async def ping(self):
        return True

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, ex=None):
        self.store[k] = v


@pytest.fixture
def embed_env(monkeypatch):
    fake_cc = CacheClient(_FakeRedis())
    import app.clients.cache_client as ccmod
    monkeypatch.setattr(ccmod, "get_cache_client", lambda: fake_cc)
    monkeypatch.setattr(settings, "embedding_strategy", "unified")
    calls = {"n": 0}

    async def fake_embed(texts, *, timeout=120.0):
        calls["n"] += 1
        return [[0.1, 0.2]]

    monkeypatch.setattr(ec, "embed_doc_texts", fake_embed)
    monkeypatch.setattr(ec, "enabled", lambda: True)
    return fake_cc, calls


@pytest.mark.asyncio
async def test_second_call_hits_cache(embed_env):
    fake_cc, calls = embed_env
    v1 = await ec.query_embed("Hello Vector")
    v2 = await ec.query_embed("hello   vector ")       # 归一化后同键
    assert v1 == v2 == {"unified": [0.1, 0.2]}
    assert calls["n"] == 1                              # 第二次零外呼


@pytest.mark.asyncio
async def test_all_none_result_not_cached(embed_env, monkeypatch):
    fake_cc, calls = embed_env

    async def fail_embed(texts, *, timeout=120.0):
        raise RuntimeError("api down")

    monkeypatch.setattr(ec, "embed_doc_texts", fail_embed)
    v = await ec.query_embed("q")
    assert v == {"unified": None}                       # 既有降级语义不变
    # 全 None 不缓存：恢复后下一次调用要真跑（而不是吃到 24h 的 None）
    monkeypatch.setattr(ec, "embed_doc_texts", AsyncMock(return_value=[[0.5]]))
    v2 = await ec.query_embed("q")
    assert v2 == {"unified": [0.5]}
