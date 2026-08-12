"""CitationEnforcer 纯函数单测（M34）—— 无 infra/网络。"""
from __future__ import annotations

from app.agent.citation_enforcer import extract_identifiers


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
