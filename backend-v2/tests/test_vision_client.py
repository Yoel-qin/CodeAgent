"""vision_client 单测：软失败四路（无 key/HTTP 错/超时/空回复）+ 成功路 + 回落语义。"""
from app.clients import vision_client
from app.core.config import settings


class _Resp:
    def __init__(self, payload=None, raise_on_status=False):
        self._payload = payload
        self._raise = raise_on_status

    def raise_for_status(self):
        if self._raise:
            raise RuntimeError("http 500")

    def json(self):
        return self._payload


def test_disabled_returns_none_without_call(monkeypatch):
    monkeypatch.setattr(settings, "vision_desc_enabled", False)
    monkeypatch.setattr(settings, "vision_api_key", "")
    monkeypatch.setattr(settings, "embedding_api_key", "")
    called = []
    monkeypatch.setattr(vision_client.httpx, "post", lambda *a, **k: called.append(a))
    assert vision_client.describe_image(b"x") is None
    assert called == []


def test_success_returns_content(monkeypatch):
    monkeypatch.setattr(settings, "vision_desc_enabled", True)
    monkeypatch.setattr(settings, "vision_api_key", "sk-t")
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"], captured["json"], captured["auth"] = url, json, headers
        return _Resp({"choices": [{"message": {"content": "  截图描述：Eclipse 安装界面  "}}]})

    monkeypatch.setattr(vision_client.httpx, "post", _fake_post)
    out = vision_client.describe_image(b"imgbytes", ext="png")
    assert out == "截图描述：Eclipse 安装界面"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"]["Authorization"] == "Bearer sk-t"
    body = captured["json"]
    assert body["model"] == "PaddlePaddle/PaddleOCR-VL-1.5"
    content = body["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_falls_back_to_embedding_credentials(monkeypatch):
    """vision_api_key 空 → 回落 embedding_api_key（同服务商字段级回落）。"""
    monkeypatch.setattr(settings, "vision_desc_enabled", True)
    monkeypatch.setattr(settings, "vision_api_key", "")
    monkeypatch.setattr(settings, "embedding_api_key", "sk-emb")
    monkeypatch.setattr(settings, "vision_base_url", "")
    monkeypatch.setattr(settings, "embedding_base_url", "https://api.siliconflow.cn/v1")
    seen = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        seen["url"], seen["auth"] = url, headers
        return _Resp({"choices": [{"message": {"content": "描述"}}]})

    monkeypatch.setattr(vision_client.httpx, "post", _fake_post)
    assert vision_client.describe_image(b"x") == "描述"
    assert seen["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert seen["auth"]["Authorization"] == "Bearer sk-emb"


def test_http_error_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "vision_desc_enabled", True)
    monkeypatch.setattr(settings, "vision_api_key", "k")
    monkeypatch.setattr(vision_client.httpx, "post",
                        lambda *a, **k: _Resp(raise_on_status=True))
    assert vision_client.describe_image(b"x") is None


def test_timeout_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "vision_desc_enabled", True)
    monkeypatch.setattr(settings, "vision_api_key", "k")

    def _boom(*a, **k):
        raise TimeoutError("t")

    monkeypatch.setattr(vision_client.httpx, "post", _boom)
    assert vision_client.describe_image(b"x") is None


def test_empty_content_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "vision_desc_enabled", True)
    monkeypatch.setattr(settings, "vision_api_key", "k")
    monkeypatch.setattr(vision_client.httpx, "post",
                        lambda *a, **k: _Resp({"choices": [{"message": {"content": "  "}}]}))
    assert vision_client.describe_image(b"x") is None
