"""M44 model_router 组合层测试：endpoint_for 三档 + legacy_client_for 构造。零网络零密钥。"""
from __future__ import annotations

from app.clients.llm_client import LLMClient
from app.clients.model_adapter import ModelEndpoint
from app.clients.model_router import endpoint_for, legacy_client_for
from app.core.config import settings

_VLLM = '{"routing": {"base_url": "http://localhost:8000/v1", "model": "qwen2.5-7b"}}'


def test_endpoint_for_empty_routes_equals_defaults(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", "")
    monkeypatch.setattr(settings, "llm_model_reasoning", "tier-c")
    ep = endpoint_for("reasoning")
    assert ep == ModelEndpoint(base_url=settings.llm_base_url.rstrip("/"),
                               api_key=settings.llm_api_key, model="tier-c")


def test_endpoint_for_routes_override(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", _VLLM)
    ep = endpoint_for("routing")
    assert ep.model == "qwen2.5-7b"
    assert ep.base_url == "http://localhost:8000/v1"
    assert endpoint_for("reasoning").model == settings.llm_model  # 未覆盖档全默认


def test_legacy_client_for_extraction(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", _VLLM)
    c = legacy_client_for("extraction")   # extraction 未在 routes 里 → 全默认
    assert isinstance(c, LLMClient)
    assert c.base_url == settings.llm_base_url.rstrip("/")
    assert c.model == settings.llm_model


def test_legacy_client_for_no_dummy_key(monkeypatch):
    """legacy 路径不套 'EMPTY' 哑钥匙：空 key → configured=False 降级照旧。"""
    monkeypatch.setattr(settings, "model_routes",
                        '{"routing": {"base_url": "http://vllm:8000/v1", "model": "q"}}')
    monkeypatch.setattr(settings, "llm_api_key", "")
    c = legacy_client_for("routing")
    assert c.api_key == ""
    assert c.configured is False


def test_llm_client_default_resolves_reasoning_routes(monkeypatch):
    """模块单例同款构造路径：默认构造经 adapter 解析 reasoning 档（不 import model_router）。"""
    monkeypatch.setattr(settings, "model_routes",
                        '{"reasoning": {"base_url": "http://vllm:8000/v1", "api_key": "k2", "model": "qwen-72b"}}')
    c = LLMClient()
    assert c.base_url == "http://vllm:8000/v1"
    assert c.api_key == "k2"
    assert c.model == "qwen-72b"


def test_llm_client_default_empty_routes_unchanged(monkeypatch):
    """MODEL_ROUTES 空 = 旧默认逐字节一致（回归锚，对照 test_model_tiers 旧断言）。"""
    monkeypatch.setattr(settings, "model_routes", "")
    monkeypatch.setattr(settings, "llm_model_reasoning", "tier-c")
    monkeypatch.setattr(settings, "llm_model", "base-m")
    c = LLMClient(api_key="k")
    assert c.model == "tier-c"
    assert c.base_url == settings.llm_base_url.rstrip("/")
