"""M42 model_for 三档 seam 测试（ChatOpenAI 离线构造，零网络）。"""

import json

import pytest

from app.agent import llm as agent_llm
from app.core.config import settings


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch):
    """CI 零密钥环境：ChatOpenAI 构造期即校验 api_key（openai>=1 空串=缺失即抛），
    离线构造需 dummy key——与 test_llm_client_usage 的 "k" 同一模式，不发起任何网络。"""
    monkeypatch.setattr(settings, "llm_api_key", "ci-dummy")


def _reset_cache(monkeypatch):
    monkeypatch.setattr(agent_llm, "_TIER_MODELS", {})


def test_model_for_defaults_to_llm_model(monkeypatch):
    _reset_cache(monkeypatch)
    for p in ("routing", "extraction", "reasoning"):
        monkeypatch.setattr(settings, f"llm_model_{p}", "")
    m = agent_llm.model_for("routing")
    assert m.model_name == settings.llm_model   # 三档默认同模型 = 零行为变更


def test_model_for_override_per_purpose(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(settings, "llm_model_routing", "tier-a")
    monkeypatch.setattr(settings, "llm_model_extraction", "tier-b")
    monkeypatch.setattr(settings, "llm_model_reasoning", "")
    assert agent_llm.model_for("routing").model_name == "tier-a"
    assert agent_llm.model_for("extraction").model_name == "tier-b"
    assert agent_llm.model_for("reasoning").model_name == settings.llm_model


def test_model_for_singleton_cache(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(settings, "llm_model_routing", "tier-a")
    assert agent_llm.model_for("routing") is agent_llm.model_for("routing")


def test_get_chat_model_is_reasoning_alias(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(settings, "llm_model_reasoning", "tier-c")
    assert agent_llm.get_chat_model().model_name == "tier-c"


def test_llm_client_reasoning_default(monkeypatch):
    """legacy httpx 客户端：reasoning 档非空时为默认生成模型。"""
    from app.clients.llm_client import LLMClient
    monkeypatch.setattr(settings, "llm_model_reasoning", "tier-c")
    monkeypatch.setattr(settings, "llm_model", "base-m")
    c = LLMClient(api_key="k")
    assert c.model == "tier-c"
    monkeypatch.setattr(settings, "llm_model_reasoning", "")
    c2 = LLMClient(api_key="k")
    assert c2.model == "base-m"


def test_usage_from_response_prefers_llm_output():
    class Msg:
        usage_metadata = {"input_tokens": 7, "output_tokens": 3}

    class Gen:
        message = Msg()
        text = "abc"

    class Resp:
        llm_output = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        generations = [[Gen()]]

    assert agent_llm._usage_from_response(Resp()) == {
        "prompt_tokens": 10, "completion_tokens": 5}


def test_usage_from_response_falls_back_to_usage_metadata():
    class Msg:
        usage_metadata = {"input_tokens": 7, "output_tokens": 3}

    class Gen:
        message = Msg()
        text = "abc"

    class Resp:
        llm_output = None
        generations = [[Gen()]]

    assert agent_llm._usage_from_response(Resp()) == {
        "prompt_tokens": 7, "completion_tokens": 3}


def test_usage_from_response_none_when_absent():
    class Gen:
        message = None
        text = "abc"

    class Resp:
        llm_output = None
        generations = [[Gen()]]

    assert agent_llm._usage_from_response(Resp()) is None


# ---- M44：端点三元组路由（langgraph 侧）----


def _secret(m) -> str:
    """ChatOpenAI 的 api_key 是 SecretStr，取明文做断言。"""
    k = getattr(m, "openai_api_key", None) or getattr(m, "api_key", None)
    return k.get_secret_value() if hasattr(k, "get_secret_value") else str(k)


def _base_url(m) -> str:
    return str(getattr(m, "openai_api_base", None) or getattr(m, "base_url", None) or "")


_ROUTES_VLLM = json.dumps({
    "routing": {"base_url": "http://localhost:8000/v1", "model": "qwen2.5-7b-instruct"},
    "reasoning": {"base_url": "http://localhost:8000/v1", "api_key": "EMPTY",
                  "model": "qwen2.5-72b-instruct"},
})


def test_model_for_routes_to_vllm_endpoint(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(settings, "model_routes", _ROUTES_VLLM)
    r = agent_llm.model_for("routing")
    assert r.model_name == "qwen2.5-7b-instruct"
    assert _base_url(r) == "http://localhost:8000/v1"
    assert _secret(r) == "ci-dummy"        # entry.api_key 缺 → llm_api_key（autouse dummy）
    m = agent_llm.model_for("reasoning")
    assert m.model_name == "qwen2.5-72b-instruct"
    assert _secret(m) == "EMPTY"


def test_model_for_empty_key_synthesizes_dummy(monkeypatch):
    """全局零 key + 档位显式指端点 → 'EMPTY' 哑钥匙防 openai>=1 构造期抛（CI 教训）。"""
    _reset_cache(monkeypatch)
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "model_routes",
                        '{"routing": {"base_url": "http://localhost:8000/v1", "model": "q"}}')
    r = agent_llm.model_for("routing")
    assert _secret(r) == "EMPTY"


def test_model_for_cache_keyed_by_endpoint(monkeypatch):
    """缓存 key 含端点三元组：同 purpose 换 base_url 换实例（旧 (purpose,名) key 会串档）。"""
    _reset_cache(monkeypatch)
    monkeypatch.setattr(settings, "model_routes", "")
    a = agent_llm.model_for("routing")
    monkeypatch.setattr(settings, "model_routes",
                        '{"routing": {"base_url": "http://vllm:8000/v1", "model": "q"}}')
    b = agent_llm.model_for("routing")
    assert a is not b
    assert _base_url(b) == "http://vllm:8000/v1"


# ---- M44 终审：classify / collab per-tier configured 门 ----


@pytest.mark.asyncio
async def test_classify_uses_llm_when_only_routing_tier_configured(monkeypatch):
    """全局零 key + routing 档有独立端点 → classify 走 LLM 分支（不走规则兜底）。"""
    captured: dict = {}

    class _FakeStructured:
        async def ainvoke(self, messages, **kwargs):
            captured["llm_branch"] = True
            return agent_llm.IntentSchema(intent="code", needs_collab=False)

    class _FakeModel:
        def with_structured_output(self, schema):
            return _FakeStructured()

    # 全局零 key（覆盖 autouse fixture 的 ci-dummy）
    monkeypatch.setattr(settings, "llm_api_key", "")
    # routing 档独立配置
    monkeypatch.setattr(settings, "model_routes",
                        '{"routing":{"base_url":"http://v:8000/v1","api_key":"EMPTY","model":"q"}}')
    monkeypatch.setattr(agent_llm, "model_for", lambda purpose="reasoning": _FakeModel())

    out = await agent_llm.classify_intent_and_collab("查一下这个类")
    assert "llm_branch" in captured
    assert out.intent == "code"


@pytest.mark.asyncio
async def test_classify_rule_fallback_when_all_keys_empty(monkeypatch):
    """全局零 key + 无档位覆盖 → 规则兜底（回归锚点）。"""
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "model_routes", "")

    out = await agent_llm.classify_intent_and_collab("文档怎么写")
    assert out.intent == "doc"
