"""CLI：一次性补偿——重新向量化 embedding_synced=False 的 chunk 并翻回 True。

场景：嵌入/编码器曾不可用，导致部分 chunk 未能入 Milvus（向量召回路丢失它们）；
恢复可用后跑此脚本补齐。若需周期自动补偿，设 INGEST_RESYNC_ENABLED=true 让后端
lifespan 后台循环跑（见 app/main.py）。

本地:
    uv run python scripts/resync_embeddings.py              # 扫描全部未同步
    uv run python scripts/resync_embeddings.py --limit 500    # 每种 kind 限 500 条
"""
from __future__ import annotations

import argparse
import os
import sys

# 确保以 `python scripts/xxx.py` 直接运行时能 import app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.pipeline.indexing import resync_pending_embeddings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Re-embed chunks where embedding_synced=False.")
    ap.add_argument("--limit", type=int, default=None, help="每种 kind 的扫描上限")
    ap.add_argument("--no-commit-each-batch", action="store_true",
                    help="禁用每批提交（默认每批提交以持久化部分进度）")
    args = ap.parse_args(argv)

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        res = resync_pending_embeddings(
            session,
            strategy=settings.embedding_strategy,
            limit=args.limit,
            commit_each_batch=not args.no_commit_each_batch,
        )

    print(f"strategy={settings.embedding_strategy}")
    for kind, r in res.items():
        print(f"  {kind}: total={r['total']} synced={r['synced']} "
              f"failed={r['failed']} skipped={r['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
