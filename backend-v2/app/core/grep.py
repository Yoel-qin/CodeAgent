"""grep_code：rg 加速 + 纯 Python 回退的双引擎正则搜索（只读）。

rg 不在 PATH / rg 执行失败 / rg 超时 → 无声回退 Python walker（spec §2.4：
不引入硬依赖）。返回的 file 一律是相对 repo 根的 posix 路径。
"""
import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

from app.core.fs_guard import resolve_repo_path

RG_TIMEOUT_SECONDS = 10


def _run_rg(*args, **kwargs):  # 测试 monkeypatch 挂点
    return subprocess.run(*args, **kwargs)


def _glob_matches(rel_posix: str, file_glob: str) -> bool:
    """支持 '**/*.java' 与 '*.java' 两种写法：**/ 前缀剥离后 fnmatch。"""
    g = file_glob or "*"
    if g.startswith("**/"):
        g = g[3:]
    return fnmatch.fnmatch(rel_posix, g)


def _grep_python(repo_dir: Path, pattern: str, file_glob: str, case_sensitive: bool, max_results: int) -> dict:
    flags = 0 if case_sensitive else re.IGNORECASE
    rx = re.compile(pattern, flags)
    matches: list[dict] = []
    total = 0
    for dirpath, _dirnames, filenames in os.walk(repo_dir):
        for fname in filenames:
            full = Path(dirpath) / fname
            rel = full.relative_to(repo_dir).as_posix()
            if not _glob_matches(rel, file_glob):
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    total += 1
                    if len(matches) < max_results:
                        matches.append({"file": rel, "line": lineno, "content": line.rstrip("\r\n")})
    return {"matches": matches, "total_count": total, "truncated": total > len(matches), "engine": "python"}


def _grep_rg(repo_dir: Path, pattern: str, file_glob: str, case_sensitive: bool, max_results: int) -> dict | None:
    """返回 None 表示 rg 不可用/失败，调用方回退 python。"""
    rg = shutil.which("rg")
    if not rg:
        return None
    args = [rg, "-n", "--no-heading", "-e", pattern]
    if not case_sensitive:
        args.append("-i")
    if file_glob:
        args += ["--glob", file_glob]
    args.append(str(repo_dir))
    try:
        proc = _run_rg(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=RG_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc is None or proc.returncode not in (0, 1):  # 1 = 无匹配，正常
        return None
    matches: list[dict] = []
    total = 0
    for raw in proc.stdout.splitlines():
        try:
            parts = raw.split(":", 2)
            if len(parts) != 3:
                continue
            f, lineno, content = parts
            rel = Path(f).relative_to(repo_dir).as_posix()
            ln = int(lineno)
        except ValueError:
            continue
        total += 1
        if len(matches) < max_results:
            matches.append({"file": rel, "line": ln, "content": content.rstrip("\r")})
    return {"matches": matches, "total_count": total, "truncated": total > len(matches), "engine": "rg"}


def grep_code(
    repos_root: str | Path,
    repo: str,
    pattern: str,
    file_glob: str = "*.java",
    case_sensitive: bool = True,
    max_results: int = 20,
) -> dict:
    """在 <repos_root>/<repo> 下按正则搜源码。非法正则/仓库缺失 → {"error": ...}。"""
    try:
        repo_dir = resolve_repo_path(repos_root, repo)
    except ValueError as e:
        return {"error": str(e)}
    if not repo_dir.is_dir():
        return {"error": f"repo not found: {repo}"}
    try:
        re.compile(pattern)
    except re.error as e:
        return {"error": f"invalid regex: {e}"}

    res = _grep_rg(repo_dir, pattern, file_glob, case_sensitive, max_results)
    if res is None:
        res = _grep_python(repo_dir, pattern, file_glob, case_sensitive, max_results)
    return res
