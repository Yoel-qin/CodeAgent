"""read_file（500 行窗口）+ list_directory（depth≤3、条目≤500）——只读。"""
from pathlib import Path

from app.core.fs_guard import resolve_repo_path

MAX_WINDOW_LINES = 500
MAX_DEPTH = 3
MAX_ENTRIES = 500


def read_file(repos_root: str | Path, repo: str, file_path: str,
              start_line: int | None = None, end_line: int | None = None) -> dict:
    try:
        target = resolve_repo_path(repos_root, repo, file_path)
    except ValueError as e:
        return {"error": str(e)}
    if not target.exists():
        return {"error": f"file not found: {file_path}"}
    if target.is_dir():
        return {"error": f"not a file: {file_path}"}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": f"read failed: {e}"}
    lines = text.splitlines()
    total = len(lines)
    start = max(1, start_line or 1)
    requested_end = end_line if end_line is not None else total  # 请求窗口（默认=全文）
    end = min(requested_end, total)
    if end - start + 1 > MAX_WINDOW_LINES:  # 500 行硬上限 → 截断
        end = start + MAX_WINDOW_LINES - 1
    truncated = end < min(requested_end, total)
    window = lines[start - 1 : end]
    return {
        "content": "\n".join(window),
        "total_lines": total,
        "start_line": start,
        "end_line": end,
        "truncated": bool(truncated),
    }


def list_directory(repos_root: str | Path, repo: str, path: str = "", depth: int = 2) -> dict:
    try:
        target = resolve_repo_path(repos_root, repo, path)
    except ValueError as e:
        return {"error": str(e)}
    if not target.is_dir():
        return {"error": f"not a directory: {path or repo}"}
    depth = max(1, min(depth, MAX_DEPTH))
    entries: list[dict] = []
    truncated = False

    def walk(d: Path, cur_depth: int) -> None:
        nonlocal truncated
        try:
            children = sorted(d.iterdir(), key=lambda p: (0 if p.is_dir() else 1, p.name.lower()))
        except OSError:
            return
        for child in children:
            if len(entries) >= MAX_ENTRIES:
                truncated = True
                return
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "path": child.relative_to(resolve_repo_path(repos_root, repo)).as_posix(),
                "depth": cur_depth,
            })
            if child.is_dir() and cur_depth < depth:
                walk(child, cur_depth + 1)

    walk(target, 1 if path else 0)
    return {"entries": entries, "truncated": truncated}
