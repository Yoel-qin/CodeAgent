"""代码实体入库：ParsedCodeFile → dict rows → upsert code_entities 表。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.code_graph import CodeEntity
from app.pipeline.parsing.code_element import ParsedCodeFile


def entities_from_parsed(pf: ParsedCodeFile, *, repo: str, module: str) -> list[dict]:
    """将 ParsedCodeFile 转为 code_entities 表行 dict 列表。

    类实体：entity_type=kind, method_name=None, signature=None, 行段=类范围。
    方法实体：含 signature, 行段含 javadoc 起点。
    """
    rows: list[dict] = []
    for cls in pf.classes:
        rows.append({
            "repo": repo,
            "entity_type": cls.kind,
            "class_name": cls.name,
            "method_name": None,
            "module": module,
            "file_path": pf.file_path,
            "start_line": cls.start_line,
            "end_line": cls.end_line,
            "signature": None,
        })
        for m in cls.methods:
            rows.append({
                "repo": repo,
                "entity_type": "method",
                "class_name": cls.name,
                "method_name": m.name,
                "module": module,
                "file_path": pf.file_path,
                "start_line": m.start_line,
                "end_line": m.end_line,
                "signature": m.signature,
            })
    return rows


def _infer_module(file_path: str) -> str:
    """从文件路径首段推断 module（broker/... → broker），无段则 root。"""
    first = file_path.replace("\\", "/").split("/")[0]
    return first if first else "root"


def upsert_entities(session: Session, rows: list[dict]) -> dict:
    """按 UK 冲突先查后插/更新，返回 {"inserted": int, "updated": int}。

    sync Session；module 从 file_path 推断（当 row["module"] 为空时）。
    """
    inserted = 0
    updated = 0
    for row in rows:
        if not row.get("module"):
            row["module"] = _infer_module(row["file_path"])
        uk_keys = (row["repo"], row["class_name"], row["method_name"],
                   row["file_path"], row["start_line"])
        stmt = select(CodeEntity).where(
            CodeEntity.repo == uk_keys[0],
            CodeEntity.class_name == uk_keys[1],
            CodeEntity.method_name == uk_keys[2],
            CodeEntity.file_path == uk_keys[3],
            CodeEntity.start_line == uk_keys[4],
        )
        existing = session.execute(stmt).scalar_one_or_none()
        if existing is None:
            session.add(CodeEntity(**row))
            inserted += 1
        else:
            changed = False
            for k, v in row.items():
                if getattr(existing, k, None) != v:
                    setattr(existing, k, v)
                    changed = True
            if changed:
                updated += 1
    session.flush()
    return {"inserted": inserted, "updated": updated}


def walk_java_files(repo_dir: Path) -> list[Path]:
    """**/*.java 排序，排除隐藏目录。"""
    results: list[Path] = []
    for p in repo_dir.rglob("*.java"):
        # 排除隐藏目录（任何路径段以 . 开头）
        if any(part.startswith(".") for part in p.parts):
            continue
        results.append(p)
    results.sort()
    return results
