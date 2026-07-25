"""CLI：全量入库一个 Java 仓库到 PostgreSQL。

用法（容器内）:
    uv run python scripts/ingest_code.py --repo /data/repo/sample --module demo --commit abc123
本地（连容器化的 PG）:
    uv run python scripts/ingest_code.py --repo ../data/repo/sample --module demo
可选 --small-file-lines 0：强制方法级切片（默认 <200 行按整文件切片）。
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
from app.pipeline.ingest_code import ingest_java_file


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest a Java repo into PostgreSQL.")
    ap.add_argument("--repo", required=True, help="仓库根目录")
    ap.add_argument("--module", default=None, help="模块名（不传则按包名首段）")
    ap.add_argument("--commit", default="UNKNOWN", help="git commit hash")
    ap.add_argument("--small-file-lines", type=int, default=None,
                    help="覆盖小文件阈值（0=强制方法级切片）")
    ap.add_argument("--ext", default=".java", help="文件扩展名（默认 .java）")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    if not repo.exists():
        print(f"repo not found: {repo}", file=sys.stderr)
        return 2

    files = sorted(repo.rglob(f"*{args.ext}"))
    print(f"found {len(files)} {args.ext} file(s) under {repo}")

    engine = create_engine(settings.database_url_sync)
    total_files = total_chunks = 0
    with Session(engine) as session:
        for f in files:
            try:
                stats = ingest_java_file(
                    session, f, commit_hash=args.commit, repo_root=repo,
                    module_name=args.module, small_file_lines=args.small_file_lines,
                )
                total_files += 1
                total_chunks += stats["chunks"]
                print(f"  + {stats['file_path']}: classes={stats['classes']} "
                      f"chunks={stats['chunks']} (method={stats['method_chunks']})")
            except Exception as e:  # 单文件失败不阻断整体
                session.rollback()
                print(f"  ! {f}: {type(e).__name__}: {e}", file=sys.stderr)
        session.commit()

    print(f"DONE. files={total_files} chunks={total_chunks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
