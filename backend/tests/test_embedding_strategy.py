"""嵌入策略调度单测（clients/embedding_client.py）。

无外部依赖：monkeypatch 底层编码器（embed_doc_texts_sync / embed_code_sync / async 版），
仅验证 ingest_embed / query_embed 在 unified 与 dual 下分别走对编码器、返回正确键。
"""
from __future__ import annotations

import pytest

from app.clients import embedding_client
from app.core.config import settings


async def _ret(val):
    """把同步值包成 awaitable，供 monkeypatch 的假编码器返回。"""
    return val


@pytest.fixture
def reset_strategy():
    saved = settings.embedding_strategy
    yield
    settings.embedding_strategy = saved


def test_ingest_embed_unified_uses_bge_for_both(monkeypatch, reset_strategy):
    settings.embedding_strategy = "unified"
    calls: dict[str, list] = {}

    def fake_doc(texts):
        calls["doc"] = list(texts)
        return [[0.1, 0.2]] * len(texts)

    def fake_code(texts):
        calls["code"] = list(texts)
        return [[9.9]] * len(texts)

    monkeypatch.setattr(embedding_client, "embed_doc_texts_sync", fake_doc)
    monkeypatch.setattr(embedding_client, "embed_code_sync", fake_code)

    out_code = embedding_client.ingest_embed("code", ["a", "b"])
    out_doc = embedding_client.ingest_embed("doc", ["c"])

    assert "doc" in calls and "code" not in calls  # unified 全走 BGE（doc 编码器）
    assert out_code == [[0.1, 0.2], [0.1, 0.2]]
    assert out_doc == [[0.1, 0.2]]


def test_ingest_embed_dual_routes_code_to_codebert(monkeypatch, reset_strategy):
    settings.embedding_strategy = "dual"
    calls: dict[str, list] = {}

    def fake_doc(texts):
        calls["doc"] = list(texts)
        return [[0.1] * 1024] * len(texts)

    def fake_code(texts):
        calls["code"] = list(texts)
        return [[9.9] * 768] * len(texts)

    monkeypatch.setattr(embedding_client, "embed_doc_texts_sync", fake_doc)
    monkeypatch.setattr(embedding_client, "embed_code_sync", fake_code)

    out_code = embedding_client.ingest_embed("code", ["x"])
    out_doc = embedding_client.ingest_embed("doc", ["y"])

    assert calls["code"] == ["x"]          # code 走 CodeBERT（model_server）
    assert calls["doc"] == ["y"]           # doc 仍走 BGE API
    assert out_code == [[9.9] * 768]
    assert out_doc == [[0.1] * 1024]


async def test_query_embed_unified_returns_single_role(monkeypatch, reset_strategy):
    settings.embedding_strategy = "unified"
    monkeypatch.setattr(embedding_client, "enabled", lambda: True)
    monkeypatch.setattr(embedding_client, "embed_doc_texts", lambda ts: _ret([[0.1] * 1024]))

    out = await embedding_client.query_embed("q")
    assert set(out.keys()) == {"unified"}
    assert out["unified"] is not None and len(out["unified"]) == 1024


async def test_query_embed_dual_returns_both_roles(monkeypatch, reset_strategy):
    settings.embedding_strategy = "dual"
    monkeypatch.setattr(embedding_client, "code_enabled", lambda: True)
    monkeypatch.setattr(embedding_client, "enabled", lambda: True)
    monkeypatch.setattr(embedding_client, "embed_code", lambda ts: _ret([[0.1] * 768]))
    monkeypatch.setattr(embedding_client, "embed_doc_texts", lambda ts: _ret([[0.1] * 1024]))

    out = await embedding_client.query_embed("q")
    assert set(out.keys()) == {"code", "doc"}
    assert len(out["code"]) == 768 and len(out["doc"]) == 1024


async def test_query_embed_dual_degrades_when_code_unavailable(monkeypatch, reset_strategy):
    settings.embedding_strategy = "dual"
    monkeypatch.setattr(embedding_client, "code_enabled", lambda: False)   # CodeBERT 关闭
    monkeypatch.setattr(embedding_client, "enabled", lambda: True)
    monkeypatch.setattr(embedding_client, "embed_doc_texts", lambda ts: _ret([[0.1] * 1024]))

    out = await embedding_client.query_embed("q")
    assert out["code"] is None            # 代码路跳过
    assert out["doc"] is not None         # 文档路仍可用


async def test_query_embed_unified_degrades_when_api_disabled(monkeypatch, reset_strategy):
    settings.embedding_strategy = "unified"
    monkeypatch.setattr(embedding_client, "enabled", lambda: False)        # 无 BGE key
    out = await embedding_client.query_embed("q")
    assert out == {"unified": None}
