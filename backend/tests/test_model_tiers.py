"""M42 model_for 三档 seam 测试（ChatOpenAI 离线构造，零网络）。"""

from app.agent import llm as agent_llm
from app.core.config import settings


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
