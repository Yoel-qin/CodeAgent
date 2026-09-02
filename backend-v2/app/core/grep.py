"""grep_code：rg 加速 + 纯 Python 回退的双引擎正则搜索（只读）。

rg 不在 PATH / rg 执行失败 / rg 超时 → 无声回退 Python walker（spec §2.4：
不引入硬依赖）。返回的 file 一律是相对 repo 根的 posix 路径。

output_mode 三态（Claude Code Grep 工具对照）：
- ``content``（默认）→ ``{"matches": [{file,line,content}], ...}``（现行形状，逐字节兼容）
- ``files_with_matches`` → ``{"files": [去重相对路径], "total_count": 文件数, ...}``
- ``count`` → ``{"counts": [{file,count}], "total_count": 总匹配行数, ...}``

两引擎同形状。max_results 语义：content=匹配行数上限；files/count=文件数上限。
"""
import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

from app.core.fs_guard import resolve_repo_path

RG_TIMEOUT_SECONDS = 10
OUTPUT_MODES = ("content", "files_with_matches", "count")


def _run_rg(*args, **kwargs):  # 测试 monkeypatch 挂点
    return subprocess.run(*args, **kwargs)


def _glob_matches(rel_posix: str, file_glob: str) -> bool:
    """支持 '**/*.java' 与 '*.java' 两种写法：**/ 前缀剥离后 fnmatch。"""
    g = file_glob or "*"
    if g.startswith("**/"):
        g = g[3:]
    return fnmatch.fnmatch(rel_posix, g)


def _strip_repo_prefix(raw: str, repo_dir: Path) -> str:
    """剥掉 rg 输出里的 repo_dir 绝对路径前缀（Windows ``\\`` 与 Unix ``/`` 两种）。"""
    for p in (str(repo_dir) + "\\", str(repo_dir) + "/"):
        if raw.startswith(p):
            return raw[len(p):]
    return raw


def _grep_python(repo_dir: Path, pattern: str, file_glob: str, case_sensitive: bool,
                 max_results: int, output_mode: str) -> dict:
    """纯 Python 回退引擎。

    残余差异：仅裁剪隐藏条目（dot-files/dot-dirs），不遵守 .gitignore 规则。
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    rx = re.compile(pattern, flags)
    matches: list[dict] = []
    per_file: dict[str, int] = {}
    total = 0
    for dirpath, dirnames, filenames in os.walk(repo_dir):
        # 裁剪隐藏目录，使 os.walk 不再下探（与 rg 默认行为对齐）
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        filenames = [f for f in filenames if not f.startswith(".")]
        for fname in filenames:
            full = Path(dirpath) / fname
            rel = full.relative_to(repo_dir).as_posix()
            if not _glob_matches(rel, file_glob):
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_hits = 0
            for lineno, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    file_hits += 1
                    total += 1
                    if output_mode == "content" and len(matches) < max_results:
                        matches.append({"file": rel, "line": lineno, "content": line.rstrip("\r\n")})
            if file_hits:
                per_file[rel] = file_hits

    if output_mode == "files_with_matches":
        files = sorted(per_file)
        return {"files": files[:max_results], "total_count": len(files),
                "truncated": len(files) > max_results, "engine": "python"}
    if output_mode == "count":
        counts = [{"file": f, "count": per_file[f]} for f in sorted(per_file)]
        return {"counts": counts[:max_results], "total_count": total,
                "truncated": len(counts) > max_results, "engine": "python"}
    return {"matches": matches, "total_count": total, "truncated": total > len(matches), "engine": "python"}


def _rg_args(rg: str, pattern: str, file_glob: str, case_sensitive: bool, output_mode: str) -> list[str]:
    if output_mode == "files_with_matches":
        args = [rg, "--files-with-matches", "-e", pattern]
    elif output_mode == "count":
        args = [rg, "--count", "-e", pattern]
    else:
        args = [rg, "-n", "--no-heading", "-e", pattern]
    if not case_sensitive:
        args.append("-i")
    if file_glob:
        args += ["--glob", file_glob]
    return args


def _parse_rg_content(stdout: str, repo_dir: Path, max_results: int) -> dict:
    matches: list[dict] = []
    total = 0
    for raw in stdout.splitlines():
        try:
            raw = _strip_repo_prefix(raw, repo_dir)
            parts = raw.split(":", 2)
            if len(parts) != 3:
                continue
            f, lineno, content = parts
            ln = int(lineno)
        except ValueError:
            continue
        total += 1
        if len(matches) < max_results:
            matches.append({"file": f.replace("\\", "/"), "line": ln, "content": content.rstrip("\r")})
    return {"matches": matches, "total_count": total, "truncated": total > len(matches), "engine": "rg"}


def _parse_rg_files(stdout: str, repo_dir: Path, max_results: int) -> dict:
    files: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in stdout.splitlines():
        rel = _strip_repo_prefix(raw, repo_dir).replace("\\", "/")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        total += 1
        if len(files) < max_results:
            files.append(rel)
    return {"files": files, "total_count": total, "truncated": total > len(files), "engine": "rg"}


def _parse_rg_count(stdout: str, repo_dir: Path, max_results: int) -> dict:
    """``--count`` 每行 ``path:count`` —— rpartition 从右拆，盘符冒号（``D:\\...``）不干扰。"""
    counts: list[dict] = []
    total = 0
    n_files = 0
    for raw in stdout.splitlines():
        path_part, sep, cnt = raw.rpartition(":")
        if not sep or not cnt.isdigit():
            continue
        rel = _strip_repo_prefix(path_part, repo_dir).replace("\\", "/")
        if not rel:
            continue
        total += int(cnt)
        n_files += 1
        if len(counts) < max_results:
            counts.append({"file": rel, "count": int(cnt)})
    return {"counts": counts, "total_count": total, "truncated": n_files > len(counts), "engine": "rg"}


def _grep_rg(repo_dir: Path, pattern: str, file_glob: str, case_sensitive: bool,
             max_results: int, output_mode: str) -> dict | None:
    """返回 None 表示 rg 不可用/失败，调用方回退 python。"""
    rg = shutil.which("rg")
    if not rg:
        return None
    args = _rg_args(rg, pattern, file_glob, case_sensitive, output_mode)
    args.append(str(repo_dir))
    try:
        proc = _run_rg(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=RG_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc is None or proc.returncode not in (0, 1):  # 1 = 无匹配，正常
        return None
    if output_mode == "files_with_matches":
        return _parse_rg_files(proc.stdout, repo_dir, max_results)
    if output_mode == "count":
        return _parse_rg_count(proc.stdout, repo_dir, max_results)
    return _parse_rg_content(proc.stdout, repo_dir, max_results)


def grep_code(
    repos_root: str | Path,
    repo: str,
    pattern: str,
    file_glob: str = "*.java",
    case_sensitive: bool = True,
    max_results: int = 20,
    output_mode: str = "content",
) -> dict:
    """在 <repos_root>/<repo> 下按正则搜源码。非法正则/仓库缺失/非法 output_mode → {"error": ...}。"""
    if output_mode not in OUTPUT_MODES:
        return {"error": f"invalid output_mode: {output_mode}"}
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

    res = _grep_rg(repo_dir, pattern, file_glob, case_sensitive, max_results, output_mode)
    if res is None:
        res = _grep_python(repo_dir, pattern, file_glob, case_sensitive, max_results, output_mode)
    return res
