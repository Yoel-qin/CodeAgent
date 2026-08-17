"""M41：llm_client usage 真值捕获（mock httpx SSE 行流）。"""
from __future__ import annotations

import json

import pytest

from app.clients.llm_client import LLMClient


def _sse(objs: list[dict]) -> list[str]:
    return [f"data: {json.dumps(o)}" for o in objs] + ["data: [DONE]"]


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    """duck-type httpx.AsyncClient.stream()；按本测试需要注入响应行。"""
    lines: list[str] = []

    def __init__(self, *args, **kwargs) -> None: ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kw):
        payload = kw["json"]
        assert payload["stream_options"] == {"include_usage": True}
        return _FakeStream(type(self).lines)


@pytest.fixture()
def patched(monkeypatch):
    def _set(lines: list[str]):
        _FakeAsyncClient.lines = lines
        monkeypatch.setattr("app.clients.llm_client.httpx.AsyncClient", _FakeAsyncClient)
    return _set


async def test_stream_tokens_captures_usage(monkeypatch, patched):
    monkeypatch.setattr("app.core.config.settings", type("S", (), {
        "llm_base_url": "http://x", "llm_api_key": "k", "llm_model": "m"}))
    client = LLMClient()
    patched(_sse([
        {"choices": [{"delta": {"content": "你好"}}]},
        {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 12, "completion_tokens": 3}},
    ]))
    out: dict = {}
    toks = [t async for t in client.stream_tokens([{"role": "user", "content": "q"}],
                                                  usage_out=out)]
    assert toks == ["你好"]
    assert out == {"prompt_tokens": 12, "completion_tokens": 3}


async def test_stream_tokens_no_usage_leaves_out_empty(monkeypatch, patched):
    monkeypatch.setattr("app.core.config.settings", type("S", (), {
        "llm_base_url": "http://x", "llm_api_key": "k", "llm_model": "m"}))
    client = LLMClient()
    patched(_sse([{"choices": [{"delta": {"content": "a"}}]}]))
    out: dict = {}
    _ = [t async for t in client.stream_tokens([{"role": "user", "content": "q"}],
                                               usage_out=out)]
    assert out == {}


async def test_chat_meta_returns_text_and_usage(monkeypatch, patched):
    monkeypatch.setattr("app.core.config.settings", type("S", (), {
        "llm_base_url": "http://x", "llm_api_key": "k", "llm_model": "m"}))
    client = LLMClient()
    patched(_sse([
        {"choices": [{"delta": {"content": "A"}}]},
        {"choices": [{"delta": {"content": "B"}}]},
        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}},
    ]))
    text, usage = await client.chat_meta([{"role": "user", "content": "q"}])
    assert text == "AB"
    assert usage == {"prompt_tokens": 9, "completion_tokens": 2}


async def test_chat_meta_without_usage(monkeypatch, patched):
    monkeypatch.setattr("app.core.config.settings", type("S", (), {
        "llm_base_url": "http://x", "llm_api_key": "k", "llm_model": "m"}))
    client = LLMClient()
    patched(_sse([{"choices": [{"delta": {"content": "X"}}]}]))
    text, usage = await client.chat_meta([{"role": "user", "content": "q"}])
    assert text == "X" and usage is None
