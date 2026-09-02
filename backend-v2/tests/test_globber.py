import shutil
from pathlib import Path

from app.core.globber import glob_files

FIX = Path(__file__).parent / "fixtures"


def test_glob_java_recursive():
    res = glob_files(FIX, "mini_repo", "**/*.java")
    assert "com/example/broker/CommitLog.java" in res["files"]
    assert res["truncated"] is False


def test_glob_ignore_and_max():
    res = glob_files(FIX, "mini_repo", "**/*", ignore_globs=["**/broker/**"], max_results=3)
    assert all(not f.startswith("com/example/broker") for f in res["files"])
    assert len(res["files"]) == 3 and res["truncated"] is True


def test_glob_missing_repo_error():
    assert "error" in glob_files(FIX, "no_such_repo", "**/*.java")


def test_glob_root_level_only():
    """'*.md' 不带 **/ 前缀 → 只匹配仓库根层。"""
    res = glob_files(FIX, "mini_repo", "*.md")
    assert res["files"] == ["README.md"]
    assert res["total_count"] == 1


def test_glob_dir_scoped():
    res = glob_files(FIX, "mini_repo", "com/example/broker/*.java")
    assert res["files"] == [
        "com/example/broker/CommitLog.java",
        "com/example/broker/FlushService.java",
    ]


def test_glob_files_sorted_deterministic():
    res1 = glob_files(FIX, "mini_repo", "**/*")
    res2 = glob_files(FIX, "mini_repo", "**/*")
    assert res1["files"] == sorted(res1["files"])
    assert res1["files"] == res2["files"]


def test_glob_skips_hidden(tmp_path):
    """隐藏目录/文件不进结果（与 grep Python 引擎同语义）。"""
    repo = tmp_path / "f" / "mini_repo"
    shutil.copytree(FIX / "mini_repo", repo)
    (repo / ".git").mkdir()
    (repo / ".git" / "Hidden.java").write_text("x")
    (repo / ".secret.md").write_text("x")
    res = glob_files(tmp_path / "f", "mini_repo", "**/*")
    assert all(".git" not in f and not f.startswith(".") for f in res["files"])
    assert res["total_count"] == 6  # fixture 原有 6 个文件
