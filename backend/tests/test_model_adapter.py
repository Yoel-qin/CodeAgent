"""M44 model_adapter 测试：MODEL_ROUTES 解析软失败 + 字段级回落合并。零网络零密钥。"""
from __future__ import annotations

import pytest

from app.clients.model_adapter import (
    TIERS,
    ModelEndpoint,
    parse_model_routes,
    resolve_endpoint,
)
from app.core.config import settings


def test_tiers_whitelist():
    assert TIERS == ("routing", "extraction", "reasoning")


def test_parse_empty_and_blank():
    assert parse_model_routes("") == {}
    assert parse_model_routes("   ") == {}


def test_parse_valid_full():
    raw = '{"routing": {"base_url": "http://vllm:8000/v1", "api_key": "EMPTY", "model": "qwen"}}'
    assert parse_model_routes(raw) == {
        "routing": {"base_url": "http://vllm:8000/v1", "api_key": "EMPTY", "model": "qwen"},
    }


def test_parse_invalid_json_softfails():
    assert parse_model_routes("{not json") == {}


def test_parse_non_dict_top_level_softfails():
    assert parse_model_routes('["routing"]') == {}


def test_parse_unknown_tier_dropped():
    raw = '{"routing": {"model": "a"}, "embed": {"model": "b"}}'  # embed 不在白名单 → 忽略该档
    assert parse_model_routes(raw) == {"routing": {"model": "a"}}


def test_parse_non_dict_entry_dropped():
    raw = '{"routing": "qwen"}'  # 值非对象 → 忽略该档
    assert parse_model_routes(raw) == {}


def test_parse_keeps_only_known_fields():
    raw = '{"routing": {"model": "a", "temperature": 0.1}}'  # 未知字段静默丢弃
    assert parse_model_routes(raw) == {"routing": {"model": "a"}}


def test_parse_cache_same_string():
    assert parse_model_routes('{"routing": {"model": "a"}}') is \
        parse_model_routes('{"routing": {"model": "a"}}')  # lru_cache 按 raw 键


def test_resolve_unknown_tier_raises():
    with pytest.raises(ValueError, match="未知模型档位"):
        resolve_endpoint("embed", {})


def test_resolve_empty_routes_equals_defaults(monkeypatch):
    monkeypatch.setattr(settings, "llm_model_reasoning", "tier-c")
    monkeypatch.setattr(settings, "llm_model_extraction", "")
    ep = resolve_endpoint("reasoning", {})
    assert ep == ModelEndpoint(
        base_url=settings.llm_base_url.rstrip("/"),
        api_key=settings.llm_api_key,
        model="tier-c",          # entry.model 缺 → llm_model_reasoning（M42 名字回落链）
    )
    ep2 = resolve_endpoint("extraction", {})
    assert ep2.model == settings.llm_model      # 名字档也空 → llm_model


def test_resolve_fieldwise_merge(monkeypatch):
    monkeypatch.setattr(settings, "llm_model_routing", "name-tier")
    entry = {"base_url": "http://vllm:8000/v1", "model": "qwen2.5-7b"}  # api_key 缺 → llm_api_key
    ep = resolve_endpoint("routing", {"routing": entry})
    assert ep.base_url == "http://vllm:8000/v1"
    assert ep.api_key == settings.llm_api_key
    assert ep.model == "qwen2.5-7b"          # entry.model 覆盖名字档
    # 只覆盖 base_url，model 走名字档回落
    ep2 = resolve_endpoint("routing", {"routing": {"base_url": "http://x:1/v1"}})
    assert ep2.model == "name-tier"
    assert ep2.base_url == "http://x:1/v1"


def test_resolve_routes_none_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, "model_routes",
                        '{"routing": {"model": "from-settings"}}')
    assert resolve_endpoint("routing").model == "from-settings"
