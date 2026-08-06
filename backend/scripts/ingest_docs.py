"""CLI：全量入库文档（markdown/pdf/docx/txt/...）到 PostgreSQL。

用法:
  uv run python scripts/ingest_docs.py --repo ../data/repo/sample --doc-type design
  uv run python scripts/ingest_docs.py --repo ../data/repo/sample/docs --ext .pdf --ext .docx --ext .txt

实现：薄封装 app.pipeline.ingest.ingest_repo（exts={ext:"doc"}），按扩展名路由多格式解析。
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
from app.pipeline.ingest import ingest_repo


def _norm_ext(e: str) -> str:
    e = e.strip().lower()
    return e if e.startswith(".") else f".{e}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest documents (md/pdf/docx/txt/...) into PostgreSQL.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--doc-type", default=None)
    ap.add_argument("--commit", default="UNKNOWN")
    ap.add_argument("--ext", action="append", default=None,
                    help="文档扩展名，可重复（默认 .md；如 --ext .pdf --ext .docx --ext .txt）")
    args = ap.parse_args(argv)

    exts_list = [_norm_ext(e) for e in (args.ext or [".md"])]

    repo = Path(args.repo)
    if not repo.exists():
        print(f"repo not found: {repo}", file=sys.stderr)
        return 2

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        stats = ingest_repo(
            session, repo, commit_hash=args.commit, doc_type=args.doc_type,
            build_relations=False, exts={e: "doc" for e in exts_list},
        )
        session.commit()

    total_chunks = total_anchors = 0
    for d in stats["details"]:
        total_chunks += d.get("chunks", 0)
        total_anchors += d.get("anchors", 0)
        print(f"  + {d['file_path']}: chunks={d.get('chunks')} anchors={d.get('anchors')}")
    for err in stats["errors"]:
        print(f"  ! {err['file']}: {err['error']}", file=sys.stderr)

    print(f"DONE. chunks={total_chunks} anchors={total_anchors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
