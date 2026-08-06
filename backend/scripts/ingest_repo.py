"""CLI：统一入库一个仓库（代码 .java + 文档 .md）到 PostgreSQL，并构建关联。

代码/文档入库内部已含 PG→ES→Milvus 一致性写入（收敛在 app.pipeline.indexing）；
瞬时不可用导致 embedding_synced=False 的 chunk 由 scripts/resync_embeddings.py 或
后端 lifespan 补偿循环重试。

本地（连容器化 PG）:
    uv run python scripts/ingest_repo.py --repo ../data/repo/sample --module demo
可选 --no-relations 跳过关联构建；--small-file-lines 0 强制代码方法级切片。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 确保以 `python scripts/xxx.py` 直接运行时能 import app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.pipeline.ingest import ingest_repo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest a repo (code + docs) into PostgreSQL.")
    ap.add_argument("--repo", required=True, help="仓库根目录")
    ap.add_argument("--module", default=None, help="代码模块名（不传则按包名首段）")
    ap.add_argument("--commit", default="UNKNOWN", help="git commit hash")
    ap.add_argument("--doc-type", default=None, help="文档类型")
    ap.add_argument("--small-file-lines", type=int, default=None,
                    help="覆盖代码小文件阈值（0=强制方法级切片）")
    ap.add_argument("--no-relations", action="store_true", help="跳过关联（锚点+调用图）构建")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    if not repo.exists():
        print(f"repo not found: {repo}", file=sys.stderr)
        return 2

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        stats = ingest_repo(
            session, repo, module=args.module, commit_hash=args.commit,
            doc_type=args.doc_type, small_file_lines=args.small_file_lines,
            build_relations=not args.no_relations,
        )
        session.commit()

    code = stats["code"]
    doc = stats["doc"]
    print(f"code: files={code['files']} chunks={code['chunks']}")
    print(f"doc:  files={doc['files']} chunks={doc['chunks']}")
    if stats.get("relations"):
        print(f"relations: {stats['relations']}")
    for err in stats["errors"]:
        print(f"  ! {err['file']}: {err['error']}", file=sys.stderr)
    print(f"DONE{' with ' + str(len(stats['errors'])) + ' error(s)' if stats['errors'] else ''}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
