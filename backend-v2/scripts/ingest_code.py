"""CLI：解析 Java 仓库 → code_entities 入库。

Usage:
    uv run python scripts/ingest_code.py --repo ../data/repo/sample
    uv run python scripts/ingest_code.py --repo ../data/repo/sample --entities-only
    uv run python scripts/ingest_code.py --repo ../data/repo/sample --batch-size 100
"""
from __future__ import annotations

import argparse
import sys
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
from app.pipeline.ingest_code import (  # noqa: E402
    entities_from_parsed,
    upsert_entities,
    walk_java_files,
)
from app.pipeline.parsing.code_parser import parse_java  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="解析 Java 仓库 → code_entities 入库")
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

    with Session(engine) as session:
        batch_rows: list[dict] = []
        for i, fp in enumerate(java_files, 1):
            try:
                src = fp.read_text(encoding="utf-8")
                rel = fp.relative_to(repo_dir).as_posix()
                pf = parse_java(src, rel)
                module = pf.module_name or _infer_module(rel)
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

    logger.info(f"完成: inserted={total_inserted}, updated={total_updated}")


def _infer_module(file_path: str) -> str:
    first = file_path.replace("\\", "/").split("/")[0]
    return first if first else "root"


if __name__ == "__main__":
    main()
