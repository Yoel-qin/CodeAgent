"""CLI：解析 Java 仓库 → code_entities + call_edges + code_metrics 入库。

三阶段主体抽为 :func:`app.pipeline.ingest_code.run_full_code_ingest`（Task 13），
CLI 与 Worker C 共用同一实现——本文件只剩参数解析 + 目录校验。

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
from app.pipeline.ingest_code import run_full_code_ingest  # noqa: E402


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

    engine = create_engine(settings.postgres_dsn_sync)
    with Session(engine) as session:
        run_full_code_ingest(
            session,
            repo=repo_dir.name,
            repo_dir=repo_dir,
            batch_log_every=args.batch_size,
            entities_only=args.entities_only,
        )


if __name__ == "__main__":
    main()
