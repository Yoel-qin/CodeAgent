import shutil
import subprocess as sp
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


def test_rg_failure_falls_back_to_python(monkeypatch, tmp_path):
    """rg 失败时无声回退 Python 引擎。"""
    shutil.copytree(Path(FIX) / "mini_repo", tmp_path / "f" / "mini_repo")
    monkeypatch.setattr(shutil, "which", lambda _n: "/fake/rg")
    monkeypatch.setattr("app.core.grep._run_rg", lambda *a, **k: None)
    res = grep_code(tmp_path / "f", repo="mini_repo", pattern="MAX_RETRY_TIMES")
    assert any("CommitLog.java" in m["file"] for m in res.get("matches", []))


def test_rg_parsing(monkeypatch, tmp_path):
    """rg 引擎输出解析：正常行/畸形行/非数字行号/截断。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f" / "mini").mkdir(parents=True)
    repo_rel = Path("f") / "mini"
    monkeypatch.setattr("app.core.grep.resolve_repo_path", lambda *_a, **_k: repo_rel)
    jf = str(repo_rel / "com" / "CommitLog.java")
    stdout = "\n".join([
        f"{jf}:14:    public static final int MAX_RETRY_TIMES = 16;",
        "malformed-line-without-colons",
        f"{jf}:notanumber:bad line no",
        f"{jf}:20:    second match",
    ])
    fake = sp.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(shutil, "which", lambda _n: "/fake/rg")
    monkeypatch.setattr("app.core.grep._run_rg", lambda *a, **k: fake)
    res = grep_code("f", "mini", "MAX_RETRY_TIMES|second")
    assert res["engine"] == "rg"
    assert res["total_count"] == 2
    assert [m["line"] for m in res["matches"]] == [14, 20]
    assert res["matches"][0]["file"] == "com/CommitLog.java"
    res2 = grep_code("f", "mini", "MAX_RETRY_TIMES|second", max_results=1)
    assert len(res2["matches"]) == 1 and res2["truncated"] is True


def test_rg_parsing_absolute_windows_path(monkeypatch, tmp_path):
    r"""rg 输出含绝对路径前缀时正确解析（Windows 反斜杠和 Unix 正斜杠）。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f" / "mini").mkdir(parents=True)
    repo_abs = (tmp_path / "f" / "mini").resolve()
    monkeypatch.setattr("app.core.grep.resolve_repo_path", lambda *_a, **_k: repo_abs)
    # 模拟 rg 输出带绝对路径前缀（含驱动器号冒号）
    stdout = "\n".join([
        f"{repo_abs}\\com\\CommitLog.java:14:    public static final int MAX_RETRY_TIMES = 16;",
        f"{repo_abs}/com/CommitLog.java:20:    second match",
    ])
    fake = sp.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(shutil, "which", lambda _n: "/fake/rg")
    monkeypatch.setattr("app.core.grep._run_rg", lambda *a, **k: fake)
    res = grep_code("f", "mini", "MAX_RETRY_TIMES|second")
    assert res["engine"] == "rg"
    assert res["total_count"] == 2
    assert [m["line"] for m in res["matches"]] == [14, 20]
    assert res["matches"][0]["file"] == "com/CommitLog.java"
    assert res["matches"][1]["file"] == "com/CommitLog.java"


def test_python_engine_skips_dot_dirs(monkeypatch, tmp_path):
    """Python 回退引擎不搜索 .git / .hidden 等隐藏目录和文件。"""
    repo = tmp_path / "f" / "mini_repo"
    shutil.copytree(Path(FIX) / "mini_repo", repo, dirs_exist_ok=True)
    # 在隐藏目录中放入匹配内容
    git_dir = repo / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "config").write_text("MAX_RETRY_TIMES secret value")
    (repo / ".hidden.java").write_text("MAX_RETRY_TIMES hidden file")
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    res = grep_code(tmp_path / "f", repo="mini_repo", pattern="MAX_RETRY_TIMES")
    assert res["engine"] == "python"
    # 隐藏文件/目录中的匹配不应出现
    for m in res["matches"]:
        assert ".git" not in m["file"] and not m["file"].startswith(".")


def test_rg_parsing_forward_slash_prefix(monkeypatch, tmp_path):
    """rg 输出带 Unix 风格绝对路径前缀也能正确剥离。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f" / "mini").mkdir(parents=True)
    repo_abs = (tmp_path / "f" / "mini").resolve()
    monkeypatch.setattr("app.core.grep.resolve_repo_path", lambda *_a, **_k: repo_abs)
    stdout = f"{repo_abs}/com/X.java:5:some content"
    fake = sp.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(shutil, "which", lambda _n: "/fake/rg")
    monkeypatch.setattr("app.core.grep._run_rg", lambda *a, **k: fake)
    res = grep_code("f", "mini", "some")
    assert res["engine"] == "rg"
    assert res["total_count"] == 1
    assert res["matches"][0]["file"] == "com/X.java"


def test_python_files_mode(monkeypatch, tmp_path):
    """files_with_matches 模式（Python 引擎）：去重文件列表 + 文件数 total_count。"""
    res = _grep(monkeypatch, tmp_path, pattern="public", file_glob="**/*.java",
                output_mode="files_with_matches")
    assert res["engine"] == "python"
    assert res["files"] == sorted(res["files"])
    assert any(f.endswith("CommitLog.java") for f in res["files"])
    assert len(res["files"]) == 5  # fixture 全部 5 个 .java
    assert all(isinstance(f, str) for f in res["files"])
    assert res["total_count"] == len(res["files"]) and res["truncated"] is False
    res2 = _grep(monkeypatch, tmp_path, pattern="public", file_glob="**/*.java",
                 output_mode="files_with_matches", max_results=1)
    assert len(res2["files"]) == 1 and res2["truncated"] is True


def test_python_count_mode(monkeypatch, tmp_path):
    """count 模式（Python 引擎）：每文件计数 + 总匹配行数 total_count。"""
    res = _grep(monkeypatch, tmp_path, pattern="e", file_glob="**/*.java", output_mode="count")
    assert res["engine"] == "python"
    assert res["counts"] == sorted(res["counts"], key=lambda c: c["file"])
    assert res["total_count"] == sum(c["count"] for c in res["counts"])
    res_line = _grep(monkeypatch, tmp_path, pattern="e", file_glob="**/*.java")
    assert res_line["total_count"] == res["total_count"]  # count 模式数的是匹配行
    res2 = _grep(monkeypatch, tmp_path, pattern="e", file_glob="**/*.java",
                 output_mode="count", max_results=1)
    assert len(res2["counts"]) == 1 and res2["truncated"] is True
    assert res2["total_count"] == res["total_count"]  # max_results 只截文件数，不截行数统计


def test_grep_files_with_matches_mode():
    res = grep_code(FIX, "mini_repo", "putMessage", file_glob="**/*.java",
                    output_mode="files_with_matches")
    assert any("CommitLog.java" in f for f in res["files"])
    assert all(isinstance(f, str) for f in res["files"])


def test_grep_count_mode_and_invalid():
    res = grep_code(FIX, "mini_repo", "putMessage", file_glob="**/*.java", output_mode="count")
    assert any(c["count"] >= 1 for c in res["counts"] if "CommitLog" in c["file"])
    assert "error" in grep_code(FIX, "mini_repo", "x", output_mode="bogus")


def test_rg_files_with_matches_mode(monkeypatch, tmp_path):
    """rg --files-with-matches 输出解析：绝对路径剥离 + 反斜杠转 posix + max_results 截文件数。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f" / "mini").mkdir(parents=True)
    repo_abs = (tmp_path / "f" / "mini").resolve()
    monkeypatch.setattr("app.core.grep.resolve_repo_path", lambda *_a, **_k: repo_abs)
    stdout = "\n".join([
        f"{repo_abs}\\com\\CommitLog.java",
        f"{repo_abs}/com/RetryPolicy.java",
    ])
    fake = sp.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(shutil, "which", lambda _n: "/fake/rg")
    monkeypatch.setattr("app.core.grep._run_rg", lambda *a, **k: fake)
    res = grep_code("f", "mini", "x", output_mode="files_with_matches")
    assert res["engine"] == "rg"
    assert res["files"] == ["com/CommitLog.java", "com/RetryPolicy.java"]
    assert res["total_count"] == 2 and res["truncated"] is False
    res2 = grep_code("f", "mini", "x", output_mode="files_with_matches", max_results=1)
    assert res2["files"] == ["com/CommitLog.java"] and res2["truncated"] is True


def test_rg_count_mode(monkeypatch, tmp_path):
    """rg --count 输出 path:count 解析：盘符冒号不干扰（rpartition）、畸形行跳过。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f" / "mini").mkdir(parents=True)
    repo_abs = (tmp_path / "f" / "mini").resolve()
    monkeypatch.setattr("app.core.grep.resolve_repo_path", lambda *_a, **_k: repo_abs)
    stdout = "\n".join([
        f"{repo_abs}\\com\\CommitLog.java:3",
        f"{repo_abs}/com/RetryPolicy.java:2",
        "malformed-line-without-colon",
        f"{repo_abs}\\com\\Bad.java:notanumber",
    ])
    fake = sp.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(shutil, "which", lambda _n: "/fake/rg")
    monkeypatch.setattr("app.core.grep._run_rg", lambda *a, **k: fake)
    res = grep_code("f", "mini", "x", output_mode="count")
    assert res["engine"] == "rg"
    assert res["counts"] == [
        {"file": "com/CommitLog.java", "count": 3},
        {"file": "com/RetryPolicy.java", "count": 2},
    ]
    assert res["total_count"] == 5 and res["truncated"] is False
    res2 = grep_code("f", "mini", "x", output_mode="count", max_results=1)
    assert len(res2["counts"]) == 1 and res2["truncated"] is True
    assert res2["total_count"] == 5  # 截的是文件数，总行数不变
