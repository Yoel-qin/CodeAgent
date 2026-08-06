"""Git 增量同步的纯 git 助手（设计 §13.2 步骤 1–2 + M21 文档 PR 写操作）。

**无 DB 依赖**——封装 ``subprocess git``：只读四件套（``rev-parse HEAD``、
``rev-parse --verify``、``show -s --format`` 取提交元信息、``diff --name-status`` 取变更文件清单）
+ M21 新增写原语（隔离 worktree 建/拆、提交、推送、删分支）。所有调用强制
``encoding="utf-8", errors="replace"`` 规避中文 Windows GBK 坑（CLAUDE.md「中文 Windows = GBK locale」）。

不引 GitPython：git 二进制在开发机/CI 均已就位。

写原语（M21）一律经隔离 ``git worktree`` 操作——**不变异主工作区**（可能脏/共享）。
best-effort 类（推送/删分支/移除 worktree）用 ``_run_try`` 捕获 ``CalledProcessError`` → None/False，
绝不抛；编排见 ``app.services.doc_pr_service``。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# 扩展名 → kind 真相源在 parsing.router.EXT_KIND（与 ingest.DEFAULT_EXTS 共用，含 .pdf/.docx/.txt）
from app.pipeline.parsing.router import EXT_KIND as _EXT_KIND


@dataclass(frozen=True)
class FileChange:
    """单个文件的变更。

    kind ∈ {"code","doc",None}：None 表示不在本期解析范围（按扩展名忽略，调用方跳过）。
    change ∈ {"ADDED","MODIFIED","DELETED"}（回滚分类 ROLLBACK/RESTORED 由 sync_rollback 后置）。
    """

    file_path: str
    kind: str | None
    change: str


def kind_for(file_path: str) -> str | None:
    """按扩展名判定 kind（.java→code / .md→doc / 其它→None）。"""
    return _EXT_KIND.get(Path(file_path).suffix.lower())


def _run(repo: str | Path, args: list[str]) -> str:
    """在 ``repo`` 目录跑 git 只读命令，返回 stdout（utf-8，尾部换行已 strip）。"""
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True,
    )
    return out.stdout.strip()


def git_head(repo: str | Path) -> str:
    """``git rev-parse HEAD``——当前 HEAD 提交哈希。"""
    return _run(repo, ["rev-parse", "HEAD"])


def resolve_commit(repo: str | Path, ref: str) -> str | None:
    """校验 ref 是否指向一个提交，返回其完整哈希；不可解析则返回 None（不抛）。"""
    try:
        return _run(repo, ["rev-parse", "--verify", f"{ref}^{{commit}}"]) or None
    except subprocess.CalledProcessError:
        return None


def commit_meta(repo: str | Path, commit: str) -> dict:
    """取提交元信息：``{hash, time, author, message}``。

    格式 ``%H|%cI|%an|%s``：完整哈希 / ISO8601 提交时间 / 作者 / 提交标题首行。
    按 ``|`` 最多切 4 段（message 内可能含 ``|``，全部归入第 4 段）。
    """
    line = _run(repo, ["show", "-s", "--format=%H|%cI|%an|%s", commit])
    parts = line.split("|", 3)
    if len(parts) < 4:
        # 兜底：缺段补空（极端情况——如空提交信息）
        parts = [parts[0] if len(parts) > 0 else "", parts[1] if len(parts) > 1 else "",
                 parts[2] if len(parts) > 2 else "", ""]
    return {"hash": parts[0], "time": parts[1], "author": parts[2], "message": parts[3]}


def changed_files(repo: str | Path, old_commit: str, new_commit: str) -> list[FileChange]:
    """``git diff --name-status {old} {new}`` → FileChange 列表。

    用 ``--no-renames`` 让重命名落为「旧路径 D + 新路径 A」（与我们的 chunk 模型一致：
    按 file_path upsert，旧路径软删、新路径新增；内容相同的 code chunk 因 chunk_id 含
    content_hash 会被自然复用）。逐行按状态首字母分类：A→ADDED / M,T→MODIFIED / D→DELETED；
    R/C（仅在不带 --no-renames 时出现）按「旧 DELETED + 新 ADDED/MODIFIED」兼容处理；
    U（未合并）忽略并告警。
    """
    raw = _run(repo, ["diff", "--no-renames", "--name-status", old_commit, new_commit])
    out: list[FileChange] = []
    if not raw:
        return out
    for line in raw.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        code = cols[0]
        # 多字段（R/C 带相似度：R100\told\tnew）——兼容处理
        if code and code[0] in ("R", "C") and len(cols) >= 3:
            old_path, new_path = cols[1], cols[2]
            new_change = "MODIFIED" if code[0] == "R" else "ADDED"
            out.append(FileChange(old_path, kind_for(old_path), "DELETED"))
            out.append(FileChange(new_path, kind_for(new_path), new_change))
            continue
        if len(cols) < 2:
            continue
        path = cols[1]
        first = code[0] if code else ""
        if first == "A":
            change = "ADDED"
        elif first in ("M", "T"):
            change = "MODIFIED"
        elif first == "D":
            change = "DELETED"
        else:  # U（未合并）或未知——忽略并告警
            logger.warning(f"[sync_git] 忽略未识别的 diff 状态 {code!r} @ {path}")
            continue
        out.append(FileChange(path, kind_for(path), change))
    return out


# ---- 写原语（M21：文档 PR 落地，经隔离 worktree，不变异主工作区）----


def _run_try(repo: str | Path, args: list[str]) -> str | None:
    """best-effort git：失败（非零退出 / git 不在 / OS 错）→ None，不抛。

    用于推送 / 删分支 / 移除 worktree 等「尽力而为」操作——调用方据 None 降级。
    """
    try:
        return _run(repo, args)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def remote_url(repo: str | Path, name: str = "origin") -> str | None:
    """取远程 ``name`` 的 URL；无该远程 / 非 git 仓库 → None（不抛）。"""
    return _run_try(repo, ["remote", "get-url", name])


def add_worktree(
    repo: str | Path, wt: str | Path, branch: str, base: str | None = None,
) -> str:
    """在 ``repo`` 下建隔离链接工作区 ``wt`` 并新建分支 ``branch``（自 ``base``，默认 HEAD）。

    ``git -C repo worktree add -b <branch> <wt> [<base>]``。**失败抛 ``CalledProcessError``**
    （分支已存在 / base 不可解析等）——编排层据此判 PUSH_FAILED。隔离工作区有自己的 HEAD，
    其后所有 ``git -C <wt>`` 操作只动该工作区，**主工作区零扰动**。
    """
    args = ["worktree", "add", "-b", branch, str(wt)]
    if base:
        args.append(base)
    return _run(repo, args)


def remove_worktree(repo: str | Path, wt: str | Path) -> None:
    """移除隔离工作区（``--force``，best-effort，不抛——清理用，失败仅留空目录）。"""
    _run_try(repo, ["worktree", "remove", "--force", str(wt)])


def commit_worktree(
    wt: str | Path, relpath: str, message: str,
    author_name: str, author_email: str,
) -> str:
    """在隔离工作区 ``wt`` 内 ``add <relpath>`` + ``commit``（注入 user 身份，不依赖全局 git config；
    关 gpgsign 避签名失败）→ 返回新 HEAD sha。

    ``relpath`` 为仓库相对路径（doc_files.file_path，正斜杠）。提交前调用方须已把改后内容写入
    ``wt/relpath``。失败抛 ``CalledProcessError``。
    """
    _run(wt, ["add", "--", relpath])
    _run(wt, [
        "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
        "-c", "commit.gpgsign=false",
        "commit", "-m", message,
    ])
    return _run(wt, ["rev-parse", "HEAD"])


def push_branch(wt: str | Path, remote: str, branch: str) -> str | None:
    """推送分支到 ``remote``（best-effort）：成功 → 返回远程 URL（pr_url 候选）；失败/无远程 → None。

    返回的 URL 供人工点开找分支/PR（无 GitHub PR API，故「关 PR」= 删分支，见 doc_pr_service）。
    """
    if _run_try(wt, ["push", "-u", remote, branch]) is None:
        return None
    return remote_url(wt, remote) or f"{remote}:{branch}"


def delete_branch(repo: str | Path, branch: str) -> None:
    """删本地分支（``-D``，best-effort，不抛）。回滚关 PR 用。"""
    _run_try(repo, ["branch", "-D", branch])


def delete_remote_branch(repo: str | Path, remote: str, branch: str) -> bool:
    """删远程分支（best-effort）：成功 True，失败/无远程 False。回滚关 PR 用。"""
    return _run_try(repo, ["push", remote, "--delete", branch]) is not None
