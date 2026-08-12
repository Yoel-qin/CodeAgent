"""CitationEnforcer 纯函数单测（M34）—— 无 infra/网络。"""
from __future__ import annotations

from types import SimpleNamespace

from app.agent.citation_enforcer import enforce, extract_identifiers


def test_extract_dotted():
    ids = extract_identifiers("调用 com.foo.Bar.doWork 完成")
    assert "com.foo.Bar.doWork" in ids


def test_extract_pascal_class():
    ids = extract_identifiers("Account 类负责账户")
    assert "Account" in ids


def test_extract_method_call():
    ids = extract_identifiers("调用 getBalance() 取余额")
    assert "getBalance" in ids


def test_stopword_filtered():
    # Spring/Service 是常见框架词（stopword），Account 不是 → 保留
    ids = extract_identifiers("Spring Service Account")
    assert "Spring" not in ids
    assert "Service" not in ids
    assert "Account" in ids


def test_dedup_preserves_order():
    ids = extract_identifiers("Account getBalance Account getBalance")
    assert ids.count("Account") == 1
    assert ids.count("getBalance") == 1


def test_extract_empty():
    assert extract_identifiers("") == []
    assert extract_identifiers("普通中文无标识符") == []


def test_no_bare_lowercase_prose():
    # 散文小写词（无内部大写、无括号、无点）不应被当代码标识符提取
    ids = extract_identifiers("the method returns a value here")
    for prose in ("method", "returns", "value", "here"):
        assert prose not in ids
    assert ids == []


def test_capitalized_prose_words_filtered():
    # 句首大写的功能词（The/This/A）不应被 _PASCAL 当标识符漏过
    ids = extract_identifiers("The Account class uses This pattern")
    assert "The" not in ids
    assert "This" not in ids
    assert "Account" in ids


def _cit(label=None, klass=None, method=None, path=None, chunk_id="c1"):
    return {"type": "code", "chunk_id": chunk_id, "label": label,
            "class": klass, "method": method, "path": path,
            "score": 0.9, "content_type": "text"}


def test_enforce_verified_via_class_method():
    citations = [_cit(label="Account.getBalance", klass="Account", method="getBalance")]
    res = enforce("Account.getBalance 做了什么", citations)
    assert res["verified_count"] >= 1
    assert res["unverified_ids"] == []
    assert res["notice"] is None


def test_enforce_unverified_flagged():
    citations = [_cit(klass="Account", method="getBalance")]
    res = enforce("FooService.bar 处理逻辑", citations)
    assert "FooService.bar" in res["unverified_ids"]
    assert res["notice"] is not None
    assert "FooService.bar" in res["notice"]


def test_enforce_whitelist_counts_verified():
    citations = [_cit(klass="Account")]
    res = enforce("SecretCfg 取值", citations, whitelist=lambda i: i == "SecretCfg")
    assert "SecretCfg" not in res["unverified_ids"]
    assert res["notice"] is None


def test_enforce_empty_citations_no_notice():
    res = enforce("FooBar 处理", [])
    assert res["notice"] is None
    assert res["unverified_ids"] == []


def test_enforce_min_unverified_threshold():
    citations = [_cit(klass="Account")]
    # 1 个未验证，阈值 2 → 记录但不标注
    res = enforce("FooService 处理", citations, min_unverified=2)
    assert res["unverified_ids"] == ["FooService"]
    assert res["notice"] is None


def test_enforce_max_listed_truncation():
    citations = [_cit(klass="Account")]
    res = enforce("Aa Ab Ac Ad Ae", citations, max_listed=2)  # "Ac" 匹配 "Account"，故 4 个未验证
    assert res["notice"] is not None
    assert "等 4 项" in res["notice"]


def test_enforce_ratio():
    citations = [_cit(klass="Account")]
    res = enforce("Account FooBar", citations)  # Account 验证、FooBar 未验证
    assert res["verified_count"] == 1
    assert res["unverified_ids"] == ["FooBar"]
    assert res["ratio"] == round(1 / 2, 3)


# ---- Task 3: 配置开关 + 适配器接入 ----


def test_config_defaults():
    from app.core.config import settings
    assert settings.citation_enforce_enabled is False
    assert settings.citation_enforce_min_unverified == 1
    assert settings.citation_enforce_max_listed == 10


def test_enforce_into_stream_off(monkeypatch):
    import app.agent.streaming as sm
    monkeypatch.setattr(sm, "settings", SimpleNamespace(citation_enforce_enabled=False))
    meta: dict = {}
    answer, tokens = sm._enforce_into_stream("FooBar 逻辑", [], meta)
    assert answer == "FooBar 逻辑"
    assert tokens == []
    assert meta["enforcement"] == {"enabled": False}


def test_enforce_into_stream_on_with_notice(monkeypatch):
    import app.agent.streaming as sm
    monkeypatch.setattr(sm, "settings", SimpleNamespace(
        citation_enforce_enabled=True,
        citation_enforce_min_unverified=1, citation_enforce_max_listed=10))
    citations = [_cit(label="Account.getBalance", klass="Account", method="getBalance")]
    meta: dict = {}
    answer, tokens = sm._enforce_into_stream("FooService.bar 逻辑", citations, meta)
    assert tokens and tokens[0]["content"].startswith("\n\n⚠️")
    assert "FooService.bar" in answer  # notice 已并入 answer
    assert meta["enforcement"]["enabled"] is True
    assert "FooService.bar" in meta["enforcement"]["unverified_ids"]


def test_enforce_into_stream_on_all_verified(monkeypatch):
    import app.agent.streaming as sm
    monkeypatch.setattr(sm, "settings", SimpleNamespace(
        citation_enforce_enabled=True,
        citation_enforce_min_unverified=1, citation_enforce_max_listed=10))
    citations = [_cit(klass="Account", method="getBalance")]
    meta: dict = {}
    answer, tokens = sm._enforce_into_stream("Account.getBalance 做了什么", citations, meta)
    assert tokens == []                       # 无未验证 → 无 notice token
    assert answer == "Account.getBalance 做了什么"
    assert meta["enforcement"]["enabled"] is True
    assert meta["enforcement"]["unverified_ids"] == []
