"""glob_files：仓库内文件名 glob 模式匹配（Claude Code Glob 工具对照，只读）。

pattern → 正则的映射（fullmatch 相对 posix 路径）：
- ``**/`` → ``(?:.*/)?``   （任意层目录前缀，含空 —— ``**/*.java`` 命中任意深度的 .java）
- ``*``   → ``[^/]*``      （单段内任意字符，不跨 ``/``；裸 ``**`` 即两个 ``[^/]*``）
- ``?``   → ``[^/]``       （单段内单个字符）
- 其余字符 ``re.escape``

因此 ``*.md`` 只命中根层、``broker/**/*.java`` 命中 broker 下任意深度。
不引 fast-glob 等依赖：1059 文件量级 os.walk 足够。隐藏条目（dot-files /
dot-dirs）不进结果 —— 与 grep 的 Python 引擎同语义。错误契约：返回
``{"error": str}``，永不抛。
"""
import os
import re
from pathlib import Path

from app.core.fs_guard import resolve_repo_path


def _compile_glob(pattern: str) -> re.Pattern[str]:
    """把 glob 串编译为对相对 posix 路径做 fullmatch 的正则（映射见模块 docstring）。"""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def glob_files(
    repos_root: str | Path,
    repo: str,
    pattern: str,
    ignore_globs: list[str] | None = None,
    max_results: int = 100,
) -> dict:
    """在 <repos_root>/<repo> 下按 glob 模式列文件。

    返回 ``{"files": [相对 posix 路径，字典序], "total_count": 全部命中数,
    "truncated": bool}``；repo 缺失/路径越狱 → ``{"error": str}``。
    max_results 截断在排序之后（字典序确定性）；core 层信任入参，clamp 在 server 层。
    """
    try:
        repo_dir = resolve_repo_path(repos_root, repo)
    except ValueError as e:
        return {"error": str(e)}
    if not repo_dir.is_dir():
        return {"error": f"repo not found: {repo}"}

    rx = _compile_glob(pattern or "")
    ignore_rxs = [_compile_glob(g) for g in (ignore_globs or [])]

    matched: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_dir):
        # 裁剪隐藏目录，使 os.walk 不再下探（与 _grep_python 同语义）
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            rel = (Path(dirpath) / fname).relative_to(repo_dir).as_posix()
            if not rx.fullmatch(rel):
                continue
            if any(ig.fullmatch(rel) for ig in ignore_rxs):
                continue
            matched.append(rel)

    matched.sort()
    total = len(matched)
    return {"files": matched[:max_results], "total_count": total, "truncated": total > max_results}
