"""CLI：解析 Java 仓库 → code_entities + call_edges + code_metrics 入库。

Usage:
    uv run python scripts/ingest_code.py --repo ../data/repo/sample
    uv run python scripts/ingest_code.py --repo ../data/repo/sample --entities-only
    uv run python scripts/ingest_code.py --repo ../data/repo/sample --batch-size 100
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

# sys.path 自举（允许从 repo 根或 backend/ 运行）
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # pragma: no cover

from loguru import logger  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.pipeline.call_graph import build_call_edges  # noqa: E402
from app.pipeline.code_metrics import compute_metrics  # noqa: E402
from app.pipeline.ingest_code import (  # noqa: E402
    _infer_module,
    entities_from_parsed,
    upsert_entities,
    walk_java_files,
)
from app.pipeline.ingest_edges import replace_edges  # noqa: E402
from app.pipeline.parsing.code_parser import parse_java  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="解析 Java 仓库 → code_entities + call_edges + code_metrics 入库")
    parser.add_argument("--repo", required=True, help="仓库根目录（绝对/相对路径）")
    parser.add_argument("--batch-size", type=int, default=200, help="每批文件数（进度日志）")
    parser.add_argument("--entities-only", action="store_true", help="仅入库实体，不建调用图")
    args = parser.parse_args()

    repo_dir = Path(args.repo).resolve()
    if not repo_dir.is_dir():
        logger.error(f"仓库目录不存在: {repo_dir}")
        sys.exit(1)

    repo_name = repo_dir.name
    java_files = walk_java_files(repo_dir)
    if not java_files:
        logger.warning(f"未找到 Java 文件: {repo_dir}")
        return

    logger.info(f"找到 {len(java_files)} 个 Java 文件")

    engine = create_engine(settings.postgres_dsn_sync)
    total_inserted = 0
    total_updated = 0

    # Stage 1: parse + upsert entities
    parsed_files: list = []
    with Session(engine) as session:
        batch_rows: list[dict] = []
        for i, fp in enumerate(java_files, 1):
            try:
                src = fp.read_text(encoding="utf-8")
                rel = fp.relative_to(repo_dir).as_posix()
                pf = parse_java(src, rel)
                parsed_files.append(pf)
                module = _infer_module(rel) or pf.module_name
                rows = entities_from_parsed(pf, repo=repo_name, module=module)
                batch_rows.extend(rows)
            except Exception:
                logger.exception(f"解析失败: {fp}")
                continue

            if i % args.batch_size == 0 or i == len(java_files):
                result = upsert_entities(session, batch_rows)
                total_inserted += result["inserted"]
                total_updated += result["updated"]
                session.commit()
                logger.info(f"已处理 {i}/{len(java_files)}")
                batch_rows = []

    logger.info(f"Stage 1 完成: inserted={total_inserted}, updated={total_updated}")

    if args.entities_only:
        return

    # Stage 2: build + insert call edges
    from sqlalchemy import select

    from app.db.models.code_graph import CodeEntity

    edges = build_call_edges(parsed_files)
    with Session(engine) as session:
        edge_count = replace_edges(session, repo=repo_name, edges=edges)
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

    with Session(engine) as session:
        # build (class_name, method_name) → entity_id map for this repo
        stmt = select(CodeEntity.id, CodeEntity.class_name, CodeEntity.method_name).where(
            CodeEntity.repo == repo_name,
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

        if deduped:
            from sqlalchemy import text as sa_text

            with engine.begin() as conn:
                for eid, mr in deduped.items():
                    conn.execute(sa_text("""
                        INSERT INTO code_metrics (entity_id, complexity, fan_in, fan_out, loc)
                        VALUES (:eid, :c, :fi, :fo, :l)
                        ON CONFLICT (entity_id) DO UPDATE SET
                            complexity = EXCLUDED.complexity,
                            fan_in = EXCLUDED.fan_in,
                            fan_out = EXCLUDED.fan_out,
                            loc = EXCLUDED.loc
                    """), {"eid": eid, "c": mr["complexity"], "fi": mr["fan_in"], "fo": mr["fan_out"], "l": mr["loc"]})
    logger.info(f"Stage 3 完成: {len(deduped)} metric rows upserted (from {len(metric_rows)} raw)")


if __name__ == "__main__":
    main()
