"""Worker A（Task 12）：push 事件 → 变更文件展开（纯函数，不做 IO 副作用除 git diff）。

payload 两种形态（webhook 侧校验二选一）：

- **显式 files**（``{"commit_hash": ..., "files": [{"path", "status"}]}``）——直接映射，
  不碰 git（github/gitlab webhook 自带变更清单的快路径）；
- **before+after**（``{"before": <sha>, "after": <sha>}``）——
  ``git -C <repos_root>/<repo> diff --name-status <before> <after>`` 逐行解析：
  ``M\\tpath`` / ``A\\tpath`` / ``D\\tpath``；rename/copy 行 ``R100\\told\\tnew``
  拆为 ``D old`` + ``A new``（新路径按新增 ingest、旧路径按删除清理）。

产出 ``[("file", {"repo", "commit_hash", "path", "status"}), ...]``；任一变更文件以
``.java`` 结尾则追加 ``("graph_rebuild", {"repo", "commit_hash"})``（其 path 在账本里
固定 ``"__repo__"``，由 Task 13 runner 落行时补）。

repo 目录缺失 / git 调用失败 / 超时（10s）/ 不可解析输出 → :class:`WorkerError`
（runner 捕获走重试，进程不崩）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.pipeline.workers import WorkerError

_DIFF_TIMEOUT_SECONDS = 10

# rename/copy 检出码：R100/C100 ...（相似度后缀任意），都带 old+new 两列
_RENAME_CODES = ("R", "C")


def expand_push(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """把一条 push 事件展开成待处理子事件列表（kind + payload）。"""
    repo = str(payload.get("repo") or "")
    commit_hash = str(payload.get("after") or payload.get("commit_hash") or "")
    files = payload.get("files")
    if files is None:
        files = _git_diff_files(
            repo, payload.get("before"), payload.get("after")
        )

    out: list[tuple[str, dict[str, Any]]] = [
        (
            "file",
            {
                "repo": repo,
                "commit_hash": commit_hash,
                "path": f["path"],
                "status": f["status"],
            },
        )
        for f in files
    ]
    if any(str(f["path"]).endswith(".java") for f in files):
        out.append(("graph_rebuild", {"repo": repo, "commit_hash": commit_hash}))
    return out


def _git_diff_files(repo: str, before: Any, after: Any) -> list[dict[str, str]]:
    """git diff --name-status → ``[{"path", "status"}]``。"""
    if not before or not after:
        raise WorkerError(f"push payload 缺 before/after，无法展开 git diff: repo={repo!r}")
    repo_dir = Path(settings.repos_root) / repo
    if not repo_dir.is_dir():
        raise WorkerError(f"repo 目录不存在: {repo_dir}")
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                # Task 12 评审 ⚠️-1：core.quotepath 默认 true 会把非 ASCII 路径
                # C-quote 成 "docs/\346\226\207..." —— 中文文件名解析全错，关掉
                "-c",
                "core.quotepath=false",
                "diff",
                "--name-status",
                str(before),
                str(after),
            ],
            capture_output=True,
            timeout=_DIFF_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerError(
            f"git diff 超时({_DIFF_TIMEOUT_SECONDS}s): {repo}@{before}..{after}"
        ) from exc
    except OSError as exc:  # git 不在 PATH 等
        raise WorkerError(f"git 调用失败: {exc}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()[:200]
        raise WorkerError(f"git diff 失败(rc={proc.returncode}): {repo} {stderr}")
    return _parse_name_status(proc.stdout.decode(errors="replace"))


def _parse_name_status(diff_text: str) -> list[dict[str, str]]:
    """解析 ``--name-status`` 输出；rename/copy 行拆 D old + A new（见模块 docstring）。"""
    files: list[dict[str, str]] = []
    for line in diff_text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0]
        if code.startswith(_RENAME_CODES) and len(parts) >= 3:
            files.append({"path": parts[1], "status": "D"})
            files.append({"path": parts[2], "status": "A"})
        elif len(parts) >= 2:
            files.append({"path": parts[-1], "status": code[:1]})
        # 其余（无 tab 的异常行）跳过——宁可漏一条也不让脏行炸 worker
    return files
