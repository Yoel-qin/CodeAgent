"""M5 Task 12：Worker A（push 事件 → 变更文件展开）。

brief 2 个逐字：git diff 展开（tmp 下真 git init/commit）+ 显式 files 直通。
"""

import subprocess

import pytest

from app.pipeline.workers.a_files import expand_push


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "gr"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "A.java").write_text("class A {}", encoding="utf-8")
    _git(repo, "add", "."); _git(repo, "commit", "-m", "v1")  # noqa: E702 —— brief 逐字
    v1 = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                        capture_output=True, text=True, check=True).stdout.strip()
    (repo / "A.java").write_text("class A { void f() {} }", encoding="utf-8")
    (repo / "B.java").write_text("class B {}", encoding="utf-8")
    (repo / "old.md").write_text("# x", encoding="utf-8")
    _git(repo, "add", "."); _git(repo, "commit", "-m", "v2")  # noqa: E702 —— brief 逐字
    v2 = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                        capture_output=True, text=True, check=True).stdout.strip()
    return repo, v1, v2


def test_expand_push_git_diff(monkeypatch, git_repo):
    repo, v1, v2 = git_repo
    monkeypatch.setattr("app.core.config.settings.repos_root", str(repo.parent))
    out = expand_push({"repo": "gr", "before": v1, "after": v2})
    kinds = {(k, p.get("path"), p.get("status")) for k, p in out}
    assert ("file", "A.java", "M") in kinds
    assert ("file", "B.java", "A") in kinds
    assert ("file", "old.md", "A") in kinds
    assert ("graph_rebuild", None, None) in kinds or any(k == "graph_rebuild" for k, _, _ in kinds)
    assert all(p["commit_hash"] == v2 for k, p in out if k == "file")


def test_expand_push_explicit_files():
    out = expand_push({"repo": "r", "commit_hash": "abc",
                       "files": [{"path": "x.md", "status": "D"}]})
    assert out == [("file", {"repo": "r", "commit_hash": "abc", "path": "x.md", "status": "D"})]


def test_expand_push_chinese_filename(tmp_path, monkeypatch):
    """Task 12 评审 ⚠️-1：中文文件名不被 git C-quote（-c core.quotepath=false）。"""
    repo = tmp_path / "cn"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs").mkdir()
    target = repo / "docs" / "架构指南.md"
    target.write_text("# v1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "v1")
    v1 = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                        capture_output=True, text=True, check=True).stdout.strip()
    target.write_text("# v2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "v2")
    v2 = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                        capture_output=True, text=True, check=True).stdout.strip()

    monkeypatch.setattr("app.core.config.settings.repos_root", str(tmp_path))
    out = expand_push({"repo": "cn", "before": v1, "after": v2})
    paths = [p["path"] for k, p in out if k == "file"]
    assert paths == ["docs/架构指南.md"], f"应产出真实中文名，而非 C-quote 串: {paths}"
