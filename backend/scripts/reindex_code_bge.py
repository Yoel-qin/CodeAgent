"""CLI：M25 一次性——把全部代码 chunk 用 BGE-M3 重新嵌入写镜像索引 code_vectors_bge。

修 dual 模式向量对中文 NL 代码查询召回弱（M24 实测 vector_only Recall@10=0.111）：
CodeBERT 无中文，把中文 NL 查询嵌入稀疏区 → 漏召；多语言 BGE-M3 此前只搜 doc_vectors
看不到代码。本脚本给代码补一份 BGE-M3(1024d) 镜像索引，查询侧用 BGE-M3 向量额外检索它。

镜像无 embedding_synced 标志位（零迁移不加 PG 列），故按「全量」重嵌（PK upsert 幂等、可重复跑）。
首次切到 dual + 开 dual_code_bgem3_enabled 后跑一次；之后增量同步已自动双写，仅在怀疑镜像缺失时重跑。

本地（从 backend/）:
    uv run python scripts/reindex_code_bge.py              # 全量重嵌全部未删代码 chunk
    uv run python scripts/reindex_code_bge.py --limit 500   # 仅前 500 条（大库断点续跑）
"""
from __future__ import annotations

import argparse
import os
import sys

# 确保以 `python scripts/xxx.py` 直接运行时能 import app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.pipeline.indexing import reindex_code_bge  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="M25: re-embed all code chunks with BGE-M3 into the code_vectors_bge mirror collection.",
    )
    ap.add_argument("--limit", type=int, default=None, help="扫描的代码 chunk 上限（大库断点续跑）")
    args = ap.parse_args(argv)

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        res = reindex_code_bge(
            session,
            strategy=settings.embedding_strategy,
            limit=args.limit,
        )

    print(f"strategy={settings.embedding_strategy}")
    print(f"  code_bge: total={res['total']} synced={res['synced']} "
          f"failed={res['failed']} skipped={res['skipped']}")
    if res["skipped"]:
        print("  (跳过：非 dual 模式 / dual_code_bgem3_enabled 关闭 / 无 BGE-M3 API key)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
