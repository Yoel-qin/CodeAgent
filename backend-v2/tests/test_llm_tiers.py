from app.clients import llm as llm_mod
from app.clients.model_adapter import ModelEndpoint, parse_model_routes, resolve_endpoint


def test_parse_routes_bad_json_ignored():
    assert parse_model_routes("not json") == {}
    assert parse_model_routes('{"bogus_tier": {"model": "x"}}') == {}


def test_resolve_endpoint_field_fallback(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "model_routes", "")
    monkeypatch.setattr(llm_mod.settings, "llm_base_url", "https://api.deepseek.com/v1/")
    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "sk-k")
    monkeypatch.setattr(llm_mod.settings, "llm_model", "deepseek-chat")
    ep = resolve_endpoint("routing")
    assert ep == ModelEndpoint(
        base_url="https://api.deepseek.com/v1", api_key="sk-k", model="deepseek-chat")


def test_chat_model_for_empty_key_dummy_and_cache(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "model_routes", "")
    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "")
    m1 = llm_mod.chat_model_for("reasoning")
    m2 = llm_mod.chat_model_for("reasoning")
    assert m1 is m2, "同档同端点必须命中实例缓存"
    assert m1.openai_api_key.get_secret_value() == "EMPTY" or m1.openai_api_key == "EMPTY"
    assert llm_mod.configured() is False


def test_chat_model_for_routes_override(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "model_routes",
                        '{"routing": {"base_url": "http://localhost:8000/v1", '
                        '"api_key": "EMPTY", "model": "qwen2.5-7b-instruct"}}')
    llm_mod._TIER_MODELS.clear()
    m = llm_mod.chat_model_for("routing")
    assert m.model_name == "qwen2.5-7b-instruct"
    llm_mod._TIER_MODELS.clear()
