"""sync_git 单测：在 tmp_path 用 subprocess 建临时 git 仓，验证 diff 解析。

唯一需要真实 ``git`` 的测试（data/repo/sample 不是独立 git 仓库）。CI/开发机均预装 git。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.pipeline import sync_git


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


@pytest.fixture
def git_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "Tester")
    (repo / "Foo.java").write_text("class Foo { void a() {} }\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("# A\nhello\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    c0 = sync_git.git_head(repo)

    # commit 2: modify Foo.java, delete docs/a.md, add Bar.java
    (repo / "Foo.java").write_text("class Foo { void a() { int x = 1; } }\n", encoding="utf-8")
    (repo / "docs" / "a.md").unlink()
    (repo / "Bar.java").write_text("class Bar {}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")
    c1 = sync_git.git_head(repo)
    return repo, c0, c1


def test_kind_for():
    assert sync_git.kind_for("pkg/Foo.java") == "code"
    assert sync_git.kind_for("docs/a.md") == "doc"
    # Phase 1.5a：.txt/.pdf/.docx 现也归 doc（EXT_KIND 唯一真相源）
    assert sync_git.kind_for("readme.txt") == "doc"
    assert sync_git.kind_for("spec.pdf") == "doc"
    assert sync_git.kind_for("note.unknown") is None


def test_git_head_returns_full_hash(git_repo):
    repo, _c0, c1 = git_repo
    assert sync_git.git_head(repo) == c1
    assert len(c1) == 40


def test_resolve_commit_valid_and_invalid(git_repo):
    repo, c0, c1 = git_repo
    assert sync_git.resolve_commit(repo, c0) == c0
    assert sync_git.resolve_commit(repo, "deadbeefnotacommit") is None


def test_commit_meta(git_repo):
    repo, _c0, c1 = git_repo
    m = sync_git.commit_meta(repo, c1)
    assert m["hash"] == c1
    assert m["author"] == "Tester"
    assert m["message"] == "change"
    assert m["time"]  # ISO8601 非空


def test_changed_files_classifies_amd(git_repo):
    repo, c0, c1 = git_repo
    changes = {c.file_path: c for c in sync_git.changed_files(repo, c0, c1)}
    assert changes["Foo.java"].kind == "code"
    assert changes["Foo.java"].change == "MODIFIED"
    assert changes["Bar.java"].kind == "code"
    assert changes["Bar.java"].change == "ADDED"
    assert changes["docs/a.md"].kind == "doc"
    assert changes["docs/a.md"].change == "DELETED"


def test_changed_files_empty_when_same_commit(git_repo):
    repo, _c0, c1 = git_repo
    assert sync_git.changed_files(repo, c1, c1) == []
