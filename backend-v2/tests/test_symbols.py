from app.core.symbols import find_symbol

FIX = "tests/fixtures"


def test_find_type_def():
    res = find_symbol(FIX, "mini_repo", "CommitLog")
    types = [loc for loc in res["locations"] if loc["kind"] == "type"]
    assert any(loc["file"].endswith("CommitLog.java") and "class CommitLog" in loc["content"] for loc in types)


def test_find_method_def():
    res = find_symbol(FIX, "mini_repo", "putMessage")
    methods = [loc for loc in res["locations"] if loc["kind"] == "method"]
    assert any(loc["file"].endswith("CommitLog.java") for loc in methods)


def test_find_ref_finds_usages():
    res = find_symbol(FIX, "mini_repo", "retryDelay", ref_type="ref")
    assert res["locations"]
    assert all(loc["kind"] == "ref" for loc in res["locations"])
    files = {loc["file"] for loc in res["locations"]}
    assert any(f.endswith("MessageConsumer.java") for f in files)


def test_empty_symbol_error():
    res = find_symbol(FIX, "mini_repo", "")
    assert "error" in res


def test_wrong_repo_def_returns_error():
    """def 路线：两条正则都因 repo 不存在而报错时，传播错误而非返回空结果。"""
    res = find_symbol(FIX, "nonexistent_repo", "CommitLog")
    assert "error" in res


def test_def_truncation_uses_total_count(monkeypatch):
    """def 路线：total_count 来自 grep_code 返回值，不受 matches 截断影响。"""
    fake_result = {
        "matches": [{"file": "X.java", "line": 1, "content": "class X {}"}] * 50,
        "total_count": 60,
        "truncated": True,
        "engine": "python",
    }
    call_count = 0
    def fake_grep(*_a, **_kw):
        nonlocal call_count
        call_count += 1
        return fake_result
    monkeypatch.setattr("app.core.symbols.grep_code", fake_grep)
    res = find_symbol("/tmp", "r", "SomeClass")
    assert res["total_count"] == 120  # 60 + 60 (type + method)
    assert res["truncated"] is True
    assert len(res["locations"]) == 50
