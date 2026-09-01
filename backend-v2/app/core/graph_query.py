"""调用图查询（同步函数，递归 CTE）。

全部返回 dict，出错时返回 {"error": ...} 契约。
"""
from __future__ import annotations

from sqlalchemy import text

from app.core.config import settings


def _exec(sql: str, params: dict) -> list[dict]:
    """执行只读 SQL，返回 row dict 列表。出错返回 None。"""
    from sqlalchemy import create_engine

    engine = create_engine(settings.postgres_dsn_sync)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql), params)
            return [row._asdict() for row in result.fetchall()]
    except Exception:
        return None  # caller checks


_CALLEES_CTE = """
WITH RECURSIVE chain AS (
    SELECT e.id AS edge_id, e.caller_id, e.callee_id, 1 AS depth,
           ARRAY[e.caller_id] AS visited
    FROM call_edges e
    JOIN code_entities c ON c.id = e.caller_id
    WHERE c.repo = :repo AND c.class_name = :cls AND c.method_name = :m
    UNION ALL
    SELECT e.id, e.caller_id, e.callee_id, ch.depth + 1,
           ch.visited || e.callee_id
    FROM call_edges e
    JOIN chain ch ON e.caller_id = ch.callee_id
    WHERE ch.depth < :depth AND NOT (e.callee_id = ANY(ch.visited))
)
SELECT ca.class_name AS caller_class, ca.method_name AS caller_method,
       cb.class_name AS callee_class, cb.method_name AS callee_method,
       e.call_type, e.call_site_file AS file, e.call_site_line AS line, ch.depth
FROM chain ch
JOIN call_edges e ON e.id = ch.edge_id
JOIN code_entities ca ON ca.id = ch.caller_id
JOIN code_entities cb ON cb.id = ch.callee_id
"""

_CALLERS_CTE = """
WITH RECURSIVE chain AS (
    SELECT e.id AS edge_id, e.caller_id, e.callee_id, 1 AS depth,
           ARRAY[e.callee_id] AS visited
    FROM call_edges e
    JOIN code_entities c ON c.id = e.callee_id
    WHERE c.repo = :repo AND c.class_name = :cls AND c.method_name = :m
    UNION ALL
    SELECT e.id, e.caller_id, e.callee_id, ch.depth + 1,
           ch.visited || e.caller_id
    FROM call_edges e
    JOIN chain ch ON e.callee_id = ch.caller_id
    WHERE ch.depth < :depth AND NOT (e.caller_id = ANY(ch.visited))
)
SELECT ca.class_name AS caller_class, ca.method_name AS caller_method,
       cb.class_name AS callee_class, cb.method_name AS callee_method,
       e.call_type, e.call_site_file AS file, e.call_site_line AS line, ch.depth
FROM chain ch
JOIN call_edges e ON e.id = ch.edge_id
JOIN code_entities ca ON ca.id = ch.caller_id
JOIN code_entities cb ON cb.id = ch.callee_id
"""

_EDGE_LIMIT = 200


def get_callees(
    repo: str, class_name: str, method: str, depth: int = 2
) -> dict:
    """下游调用链（callee 方向），递归 CTE。"""
    try:
        rows = _exec(_CALLEES_CTE, {"repo": repo, "cls": class_name, "m": method, "depth": depth})
        if rows is None:
            return {"error": "query execution failed", "edges": [], "total": 0, "truncated": False}
        truncated = len(rows) > _EDGE_LIMIT
        return {
            "edges": rows[:_EDGE_LIMIT],
            "total": len(rows),
            "truncated": truncated,
        }
    except Exception as exc:
        return {"error": str(exc), "edges": [], "total": 0, "truncated": False}


def get_callers(
    repo: str, class_name: str, method: str, depth: int = 2
) -> dict:
    """上游调用链（caller 方向），递归 CTE。"""
    try:
        rows = _exec(_CALLERS_CTE, {"repo": repo, "cls": class_name, "m": method, "depth": depth})
        if rows is None:
            return {"error": "query execution failed", "edges": [], "total": 0, "truncated": False}
        truncated = len(rows) > _EDGE_LIMIT
        return {
            "edges": rows[:_EDGE_LIMIT],
            "total": len(rows),
            "truncated": truncated,
        }
    except Exception as exc:
        return {"error": str(exc), "edges": [], "total": 0, "truncated": False}


def get_module_deps(repo: str, module: str) -> dict:
    """模块间依赖（按 callee module 分组，top3 key classes）。"""
    sql = """
    SELECT callee.module AS module, COUNT(*) AS call_count
    FROM call_edges e
    JOIN code_entities caller ON caller.id = e.caller_id
    JOIN code_entities callee ON callee.id = e.callee_id
    WHERE caller.repo = :repo AND caller.module = :module
      AND callee.module != caller.module
    GROUP BY callee.module
    ORDER BY call_count DESC
    """
    sql_classes = """
    SELECT callee.class_name, COUNT(*) AS cnt
    FROM call_edges e
    JOIN code_entities caller ON caller.id = e.caller_id
    JOIN code_entities callee ON callee.id = e.callee_id
    WHERE caller.repo = :repo AND caller.module = :module
      AND callee.module = :dep_module
    GROUP BY callee.class_name
    ORDER BY cnt DESC
    LIMIT 3
    """
    try:
        deps = _exec(sql, {"repo": repo, "module": module})
        if deps is None:
            return {"error": "query execution failed", "dependencies": []}
        result = []
        for d in deps:
            kcs = _exec(sql_classes, {"repo": repo, "module": module, "dep_module": d["module"]})
            key_classes = [r["class_name"] for r in (kcs or [])]
            result.append({
                "module": d["module"],
                "call_count": d["call_count"],
                "key_classes": key_classes,
            })
        return {"dependencies": result}
    except Exception as exc:
        return {"error": str(exc), "dependencies": []}


def code_metrics(
    repo: str, class_name: str, method_name: str | None = None
) -> dict:
    """实体度量查询。"""
    sql = """
    SELECT ce.class_name, ce.method_name, cm.complexity, cm.fan_in, cm.fan_out, cm.loc
    FROM code_metrics cm
    JOIN code_entities ce ON ce.id = cm.entity_id
    WHERE ce.repo = :repo AND ce.class_name = :cls
    """
    params: dict = {"repo": repo, "cls": class_name}
    if method_name is not None:
        sql += " AND ce.method_name = :m"
        params["m"] = method_name
    else:
        sql += " AND ce.method_name IS NOT NULL"
    try:
        rows = _exec(sql, params)
        if rows is None:
            return {"error": "query execution failed", "entities": []}
        return {"entities": rows}
    except Exception as exc:
        return {"error": str(exc), "entities": []}
