"""代码实体入库：ParsedCodeFile → dict rows → upsert code_entities 表。

:func:`run_full_code_ingest` 是三阶段全量 ingest 的唯一实现（Task 13 抽取），
CLI（``scripts/ingest_code.py``）与 Worker C（``workers/c_graph.py``）共用——
拆分前后的行为差异（commit 粒度除外）为零：Stage 1 逐文件解析失败跳过、
批内 upsert、Stage 2 按 repo 先删后插边、Stage 3 度量 upsert。
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.db.models.code_graph import CodeEntity
from app.pipeline.call_graph import build_call_edges
from app.pipeline.code_metrics import compute_metrics
from app.pipeline.ingest_edges import replace_edges
from app.pipeline.parsing.code_element import ParsedCodeFile
from app.pipeline.parsing.code_parser import parse_java


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


def run_full_code_ingest(
    session: Session,
    *,
    repo: str,
    repo_dir: Path,
    batch_log_every: int = 200,
    entities_only: bool = False,
) -> dict:
    """三阶段全量 ingest（Task 13 从 scripts/ingest_code.py 抽取，CLI 与 Worker C 共用）。

    - Stage 1: 逐文件 parse_java（失败跳过并记日志）→ 批量 upsert code_entities；
    - Stage 2: build_call_edges → replace_edges（按 repo 先删后插）；
    - Stage 3: compute_metrics → code_metrics upsert（ON CONFLICT entity_id）。

    ``entities_only`` 是 CLI ``--entities-only`` 逃生口：只跑 Stage 1。
    长事务风险规避：每批/每阶段自行 commit（进度即落库；调用方补一次 commit 幂等）。
    返回 ``{"files", "inserted", "updated", "edges", "metrics"}``。
    """
    java_files = walk_java_files(repo_dir)
    if not java_files:
        logger.warning(f"未找到 Java 文件: {repo_dir}")
        return {"files": 0, "inserted": 0, "updated": 0, "edges": 0, "metrics": 0}

    logger.info(f"找到 {len(java_files)} 个 Java 文件")
    total_inserted = 0
    total_updated = 0
    parsed_files: list[ParsedCodeFile] = []

    # Stage 1: parse + upsert entities
    batch_rows: list[dict] = []
    for i, fp in enumerate(java_files, 1):
        try:
            src = fp.read_text(encoding="utf-8")
            rel = fp.relative_to(repo_dir).as_posix()
            pf = parse_java(src, rel)
            parsed_files.append(pf)
            module = _infer_module(rel) or pf.module_name
            batch_rows.extend(entities_from_parsed(pf, repo=repo, module=module))
        except Exception:
            logger.exception(f"解析失败: {fp}")
            continue

        if i % batch_log_every == 0 or i == len(java_files):
            result = upsert_entities(session, batch_rows)
            total_inserted += result["inserted"]
            total_updated += result["updated"]
            session.commit()
            logger.info(f"已处理 {i}/{len(java_files)}")
            batch_rows = []

    logger.info(f"Stage 1 完成: inserted={total_inserted}, updated={total_updated}")
    stats: dict = {
        "files": len(java_files),
        "inserted": total_inserted,
        "updated": total_updated,
    }
    if entities_only:
        stats.update(edges=0, metrics=0)
        return stats

    # Stage 2: build + replace call edges
    edges = build_call_edges(parsed_files)
    edge_count = replace_edges(session, repo=repo, edges=edges)
    session.commit()
    logger.info(f"Stage 2 完成: {edge_count} edges inserted")

    # Stage 3: compute + upsert metrics
    fan_in: dict[tuple[str, str], int] = defaultdict(int)
    fan_out: dict[tuple[str, str], int] = defaultdict(int)
    for e in edges:
        caller_key = (e["caller_class"], e["caller_method"])
        callee_key = (e["callee_class"], e["callee_method"])
        fan_out[caller_key] += 1
        fan_in[callee_key] += 1
    fan_in_out = {
        k: (fan_in.get(k, 0), fan_out.get(k, 0))
        for k in set(fan_in) | set(fan_out)
    }
    metric_rows = compute_metrics(parsed_files, fan_in_out)

    # build (class_name, method_name) → entity_id map for this repo
    stmt = select(CodeEntity.id, CodeEntity.class_name, CodeEntity.method_name).where(
        CodeEntity.repo == repo,
        CodeEntity.method_name.is_not(None),
    )
    id_map: dict[tuple[str, str], int] = {
        (r.class_name, r.method_name): r.id for r in session.execute(stmt).all()
    }
    # deduplicate by entity_id (last-wins), then bulk upsert via raw SQL
    deduped: dict[int, dict] = {}
    for mr in metric_rows:
        eid = id_map.get((mr["class_name"], mr["method_name"]))
        if eid is None:
            continue
        deduped[eid] = mr

    for eid, mr in deduped.items():
        session.execute(sa_text("""
            INSERT INTO code_metrics (entity_id, complexity, fan_in, fan_out, loc)
            VALUES (:eid, :c, :fi, :fo, :l)
            ON CONFLICT (entity_id) DO UPDATE SET
                complexity = EXCLUDED.complexity,
                fan_in = EXCLUDED.fan_in,
                fan_out = EXCLUDED.fan_out,
                loc = EXCLUDED.loc
        """), {"eid": eid, "c": mr["complexity"], "fi": mr["fan_in"], "fo": mr["fan_out"], "l": mr["loc"]})
    session.commit()
    logger.info(f"Stage 3 完成: {len(deduped)} metric rows upserted (from {len(metric_rows)} raw)")

    stats.update(edges=edge_count, metrics=len(deduped))
    return stats
