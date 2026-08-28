import shutil
from pathlib import Path

from app.core.grep import grep_code

FIX = "tests/fixtures"


def _grep(monkeypatch, tmp_path, **kw):
    """指向 tmp 拷贝的 fixture，且强制 python 引擎（与机器是否装 rg 无关）。"""
    shutil.copytree(Path(FIX) / "mini_repo", tmp_path / "f" / "mini_repo", dirs_exist_ok=True)
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    kw.setdefault("repo", "mini_repo")
    return grep_code(tmp_path / "f", **kw)


def test_find_constant_python_engine(monkeypatch, tmp_path):
    res = _grep(monkeypatch, tmp_path, pattern="MAX_RETRY_TIMES")
    assert res["engine"] == "python"
    files = {m["file"] for m in res["matches"]}
    assert "com/example/broker/CommitLog.java" in files
    line = next(m for m in res["matches"] if m["file"].endswith("CommitLog.java"))
    assert "MAX_RETRY_TIMES" in line["content"]


def test_file_glob_filters_out_readme(monkeypatch, tmp_path):
    res = _grep(monkeypatch, tmp_path, pattern="mini", file_glob="*.java")
    assert all(m["file"].endswith(".java") for m in res["matches"])


def test_glob_star_star_prefix(monkeypatch, tmp_path):
    res = _grep(monkeypatch, tmp_path, pattern="class RetryPolicy", file_glob="**/*.java")
    assert any("RetryPolicy.java" in m["file"] for m in res["matches"])


def test_case_insensitive(monkeypatch, tmp_path):
    res_ci = _grep(monkeypatch, tmp_path, pattern="max_retry_times", case_sensitive=False)
    assert res_ci["matches"]
    res_cs = _grep(monkeypatch, tmp_path, pattern="max_retry_times", case_sensitive=True)
    assert not res_cs["matches"]


def test_max_results_truncates(monkeypatch, tmp_path):
    res = _grep(monkeypatch, tmp_path, pattern="e", max_results=3)
    assert len(res["matches"]) == 3
    assert res["truncated"] is True
    assert res["total_count"] > 3


def test_bad_regex_returns_error(monkeypatch, tmp_path):
    res = _grep(monkeypatch, tmp_path, pattern="[unclosed")
    assert "error" in res


def test_missing_repo_error(monkeypatch, tmp_path):
    res = _grep(monkeypatch, tmp_path, pattern="x", repo="nope")
    assert "error" in res and "nope" in res["error"]


def test_rg_engine_used_when_available(monkeypatch, tmp_path):
    """rg 在 PATH 时走 rg 引擎（结果与 python 引擎一致）。"""
    shutil.copytree(Path(FIX) / "mini_repo", tmp_path / "f" / "mini_repo")
    monkeypatch.setattr(shutil, "which", lambda _n: "/fake/rg")
    monkeypatch.setattr("app.core.grep._run_rg", lambda *a, **k: None)  # 模拟 rg 失败→仍出结果
    res = grep_code(tmp_path / "f", repo="mini_repo", pattern="MAX_RETRY_TIMES")
    assert any("CommitLog.java" in m["file"] for m in res.get("matches", []))
