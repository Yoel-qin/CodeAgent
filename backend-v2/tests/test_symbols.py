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
