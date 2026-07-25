"""CLI：全量入库 Markdown 文档到 PostgreSQL。

用法: uv run python scripts/ingest_docs.py --repo ../data/repo/sample --doc-type design
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.pipeline.ingest_doc import ingest_markdown_file


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest Markdown docs into PostgreSQL.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--doc-type", default=None)
    ap.add_argument("--commit", default="UNKNOWN")
    ap.add_argument("--ext", default=".md")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    files = sorted(repo.rglob(f"*{args.ext}"))
    print(f"found {len(files)} {args.ext} file(s) under {repo}")

    engine = create_engine(settings.database_url_sync)
    total_chunks = total_anchors = 0
    with Session(engine) as session:
        for f in files:
            try:
                stats = ingest_markdown_file(session, f, commit_hash=args.commit,
                                             repo_root=repo, doc_type=args.doc_type)
                total_chunks += stats["chunks"]
                total_anchors += stats["anchors"]
                print(f"  + {stats['file_path']}: chunks={stats['chunks']} anchors={stats['anchors']}")
            except Exception as e:
                session.rollback()
                print(f"  ! {f}: {type(e).__name__}: {e}", file=sys.stderr)
        session.commit()
    print(f"DONE. chunks={total_chunks} anchors={total_anchors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
