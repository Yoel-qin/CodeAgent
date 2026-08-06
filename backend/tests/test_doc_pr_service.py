"""真实文档 PR 落地服务（M21）单测：_splice / _fulfill_sync（隔离 worktree 真 git）/ closer。

策略：``_splice`` 纯函数无 IO；``_fulfill_sync`` 在 ``tmp_path`` 用 subprocess 建临时 git 仓
（仿 ``test_sync_git.py``——data/repo/sample 非独立 git 仓库），验隔离 worktree 建分支+提交+推送
（本地 bare remote 模拟，不触真实 GitHub）+ 主工作区零扰动。``fulfill_doc_update`` 闸门 + closer
用假 session/假 engine 隔离真 DB/git。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.pipeline import sync_git
from app.services import doc_pr_service as dps


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


@pytest.fixture
def doc_repo(tmp_path: Path) -> Path:
    """临时 git 仓：tracked docs/guide.md（含 ``OLD PARA HERE``），单次 init 提交。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "Tester")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# Guide\n\nOLD PARA HERE\n\n尾段\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _payload(repo, **over) -> dict:
    base = {
        "repo": str(repo), "remote": "origin", "file_relpath": "docs/guide.md",
        "original": "OLD PARA HERE", "rewritten": "NEW PARA HERE", "branch": "coderag/doc-update-x",
        "message": "docs: update", "author_name": "CodeRAG", "author_email": "noreply@coderag.local",
        "push_enabled": False,
    }
    base.update(over)
    return base


# ---- _splice（纯函数）----


def test_splice_exact_unique_replaces():
    out = dps._splice("OLD", "NEW", "pre\nOLD\npost")
    assert out == "pre\nNEW\npost"


def test_splice_not_found_returns_none():
    assert dps._splice("MISSING", "NEW", "pre\npost") is None


def test_splice_multiple_occurrences_returns_none():
    assert dps._splice("DUP", "NEW", "DUP\nDUP") is None  # 歧义，不猜


def test_splice_crlf_drift_replaces_preserving_eol():
    # 磁盘 CRLF、original（来自 doc_chunks.content）LF → 归一化后唯一命中
    out = dps._splice("OLD\nLINE", "NEW\nLINE", "pre\r\nOLD\r\nLINE\r\npost")
    assert out is not None
    assert "NEW" in out and "\r\n" in out   # 保留 CRLF 风格


def test_splice_missing_inputs_return_none():
    assert dps._splice(None, "NEW", "x") is None
    assert dps._splice("OLD", None, "x") is None
    assert dps._splice("", "NEW", "x") is None


# ---- _fulfill_sync（隔离 worktree 真 git）----


def test_fulfill_sync_committed_no_remote(doc_repo):
    base = sync_git.git_head(doc_repo)
    res = dps._fulfill_sync(_payload(doc_repo))
    assert res["git_status"] == "COMMITTED"
    assert res["commit_sha"] and res["commit_sha"] != base
    assert res["pr_url"] is None and res["error"] is None
    # 分支存在且指向新提交
    assert sync_git.resolve_commit(doc_repo, "coderag/doc-update-x") == res["commit_sha"]
    # 主工作区 HEAD 不变（worktree 隔离）
    assert sync_git.git_head(doc_repo) == base
    # 主工作区磁盘文件未变（OLD 仍在）；分支 blob 已替换为 NEW
    assert "OLD PARA HERE" in (doc_repo / "docs" / "guide.md").read_text(encoding="utf-8")
    blob = _git_out(doc_repo, "show", "coderag/doc-update-x:docs/guide.md")
    assert "NEW PARA HERE" in blob and "OLD PARA HERE" not in blob


def test_fulfill_sync_pushed_with_bare_remote(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "Tester")
    (repo / "docs").mkdir()
    (repo / "docs" / "g.md").write_text("OLD\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    bare = tmp_path / "bare"
    bare.mkdir()
    _git(bare, "init", "-q", "--bare")
    _git(repo, "remote", "add", "origin", str(bare))
    res = dps._fulfill_sync(_payload(repo, file_relpath="docs/g.md", original="OLD", rewritten="NEW",
                                     branch="coderag/pushed", push_enabled=True))
    assert res["git_status"] == "PUSHED"
    assert res["pr_url"]                        # 远程 URL（pr_url 候选）
    # 分支已推送到 bare remote
    assert _git_out(bare, "rev-parse", "coderag/pushed") == res["commit_sha"]


def test_fulfill_sync_splice_miss_yields_push_failed_and_cleans_branch(doc_repo):
    base = sync_git.git_head(doc_repo)
    res = dps._fulfill_sync(_payload(doc_repo, original="NONEXISTENT TEXT"))
    assert res["git_status"] == "PUSH_FAILED"
    assert res["commit_sha"] is None
    assert res["error"]                         # 记失败原因
    # 空分支已清理（finally 删）；主工作区 HEAD 不变
    assert sync_git.resolve_commit(doc_repo, "coderag/doc-update-x") is None
    assert sync_git.git_head(doc_repo) == base


def test_fulfill_sync_branch_exists_yields_push_failed_keeps_existing(doc_repo):
    # 先成功建一次分支
    first = dps._fulfill_sync(_payload(doc_repo))
    assert first["git_status"] == "COMMITTED"
    first_sha = first["commit_sha"]
    # 再用同名分支 → worktree add 失败（分支已存在）→ PUSH_FAILED，且不删既有分支
    second = dps._fulfill_sync(_payload(doc_repo, rewritten="AGAIN NEW"))
    assert second["git_status"] == "PUSH_FAILED"
    assert "worktree add 失败" in second["error"]
    assert sync_git.resolve_commit(doc_repo, "coderag/doc-update-x") == first_sha  # 既有分支保留


# ---- fulfill_doc_update 闸门（async）----


async def test_fulfill_doc_update_killswitch_returns_none(monkeypatch):
    monkeypatch.setattr(dps.settings, "doc_git_enabled", False)

    class _Boom:  # kill-switch 应在访问 session 前早退
        async def execute(self, *a, **k):
            raise AssertionError("kill-switch 不应触 session")

    out = await dps.fulfill_doc_update(_Boom(), proposal_id=7)  # type: ignore[arg-type]
    assert out == {"git_status": None, "commit_sha": None, "pr_url": None, "error": None}


# ---- close_open_doc_pr_for（closer；假 engine/session + 假 git op）----


class _Rows:
    def __init__(self, rows): self._rows = rows

    def mappings(self): return self

    def all(self): return list(self._rows)


class _FakeSess:
    def __init__(self, open_rows):
        self._open = open_rows
        self.closed_pids: list = []

    def execute(self, stmt, params=None):
        if "status IN" in str(stmt):              # _OPEN_PR_SQL
            return _Rows([dict(r) for r in self._open])
        self.closed_pids.append((params or {}).get("pid"))  # _CLOSE_PR_SQL
        return _Rows([])

    def commit(self): pass


class _FakeSessCtx:
    def __init__(self, sess): self._sess = sess

    def __enter__(self): return self._sess

    def __exit__(self, *a): return False


class _FakeEngine:
    disposed = False

    def dispose(self): self.disposed = True


def test_close_pr_no_match_returns_none(monkeypatch):
    sess = _FakeSess(open_rows=[])
    monkeypatch.setattr(dps, "create_engine", lambda url: _FakeEngine())
    monkeypatch.setattr(dps, "Session", lambda engine: _FakeSessCtx(sess))
    assert dps.close_open_doc_pr_for("deadbeef") is None
    assert sess.closed_pids == []


def test_close_pr_deletes_branches_and_flips_status(monkeypatch):
    sess = _FakeSess(open_rows=[
        {"proposal_id": 1, "branch_name": "b1", "pr_url": None},     # 仅本地
        {"proposal_id": 2, "branch_name": "b2", "pr_url": "http://x"},  # 已推送
    ])
    deleted_local: list = []
    deleted_remote: list = []
    monkeypatch.setattr(dps, "create_engine", lambda url: _FakeEngine())
    monkeypatch.setattr(dps, "Session", lambda engine: _FakeSessCtx(sess))
    monkeypatch.setattr(dps.sync_git, "delete_branch",
                        lambda repo, b: deleted_local.append(b))
    monkeypatch.setattr(dps.sync_git, "delete_remote_branch",
                        lambda repo, rem, b: deleted_remote.append(b) or True)
    out = dps.close_open_doc_pr_for("abc1234")
    assert out and "2" in out                    # 摘要含关闭数
    assert deleted_local == ["b1", "b2"]         # 两分支均删
    assert deleted_remote == ["b2"]              # 仅 pr_url 非空的删远程
    assert sess.closed_pids == [1, 2]            # 两提案翻 CLOSED_BY_ROLLBACK


def test_close_pr_empty_source_returns_none():
    assert dps.close_open_doc_pr_for("") is None
