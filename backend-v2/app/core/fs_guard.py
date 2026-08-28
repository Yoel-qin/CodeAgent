"""路径监狱：一切文件类工具的入口守卫（spec §3.3 硬约束）。

规则：repo 必须是单段名；rel_path 非绝对、无 ``..`` 段；resolve 后必须落在
repos_root 内。Windows 下 ``/etc/passwd`` 与盘符路径都会被 ``is_absolute()`` 捕获。
"""
from pathlib import Path, PurePosixPath


class PathEscapeError(ValueError):
    """路径越狱（绝对路径 / .. 穿越出 repos_root / 多段 repo 名）。"""


def resolve_repo_path(repos_root: str | Path, repo: str, rel_path: str = "") -> Path:
    root = Path(repos_root).resolve()

    if not repo or len(PurePosixPath(repo).parts) != 1 or PurePosixPath(repo).parts[0] in (".", ".."):
        raise PathEscapeError(f"repo 必须是 repos_root 下的单段目录名，收到: {repo!r}")

    rel = (rel_path or "").strip()
    if rel:
        pure = PurePosixPath(rel.replace("\\", "/"))
        if pure.is_absolute() or Path(rel).is_absolute():
            raise PathEscapeError(f"rel_path 不能是绝对路径: {rel!r}")
        if ".." in pure.parts:
            raise PathEscapeError(f"rel_path 不允许 .. 穿越: {rel!r}")

    target = (root / repo / rel).resolve()
    if target != root and not target.is_relative_to(root):
        raise PathEscapeError(f"路径越出 repos_root: {target}")
    return target
