import re

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.grep import grep_code

_TYPE_RX = r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|static|final|abstract)\s+)*(?:class|interface|enum)\s+{name}\b"
_METHOD_RX = r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|static|final|abstract|synchronized|native)\s+)*[\w<>\[\],.\s]+?\s{name}\s*\("

# ── PG 模块级惰性单例（同步，与 graph_query 同模式） ─────────────────────
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.postgres_dsn_sync)
    return _engine


def _def_via_sql(repo: str, symbol_name: str) -> dict | None:
    """Try SQL lookup for def type/method. Returns result dict or None (fallback)."""
    try:
        with _get_engine().connect() as conn:
            rows_type = conn.execute(
                text(
                    "SELECT file_path, start_line, signature "
                    "FROM code_entities "
                    "WHERE repo = :repo AND class_name = :name AND method_name IS NULL"
                ),
                {"repo": repo, "name": symbol_name},
            ).fetchall()

            rows_method = conn.execute(
                text(
                    "SELECT file_path, start_line, signature "
                    "FROM code_entities "
                    "WHERE repo = :repo AND method_name = :name"
                ),
                {"repo": repo, "name": symbol_name},
            ).fetchall()

            locations = []
            for r in rows_type:
                rd = r._asdict()
                locations.append({
                    "file": rd["file_path"],
                    "line": rd["start_line"],
                    "content": rd["signature"] or "",
                    "kind": "type",
                })
            for r in rows_method:
                rd = r._asdict()
                locations.append({
                    "file": rd["file_path"],
                    "line": rd["start_line"],
                    "content": rd["signature"] or "",
                    "kind": "method",
                })

            if not locations:
                return None  # 零命中 → 回落正则

            return {
                "locations": locations,
                "total_count": len(locations),
                "truncated": False,
            }
    except Exception:
        return None  # 任何异常 → 回落正则


def find_symbol(repos_root, repo: str, symbol_name: str, ref_type: str = "def") -> dict:
    name = (symbol_name or "").strip()
    if not name:
        return {"error": "symbol_name is required"}
    esc = re.escape(name)
    if ref_type == "ref":
        res = grep_code(repos_root, repo, rf"\b{esc}\b", file_glob="*.java")
        if "error" in res:
            return res
        locations = [
            {"file": m["file"], "line": m["line"], "content": m["content"], "kind": "ref"}
            for m in res["matches"]
        ]
    else:
        # SQL 优先：表有数据则走 DB，否则回落正则
        sql_result = _def_via_sql(repo, name)
        if sql_result is not None:
            return sql_result

        locations = []
        total = 0
        errors = []
        for kind, rx in (("type", _TYPE_RX), ("method", _METHOD_RX)):
            res = grep_code(repos_root, repo, rx.format(name=esc), file_glob="*.java", max_results=50)
            if "error" in res:
                errors.append(res)
                continue
            total += res["total_count"]
            for m in res["matches"]:
                if len(locations) < 50:
                    locations.append(
                        {"file": m["file"], "line": m["line"], "content": m["content"], "kind": kind}
                    )
        if len(errors) == 2:
            return errors[0]
        return {"locations": locations, "total_count": total, "truncated": total > len(locations)}
    return {"locations": locations, "total_count": res["total_count"], "truncated": res["truncated"]}
