"""CLI：全量入库一个 Java 仓库到 PostgreSQL。

用法（容器内）:
    uv run python scripts/ingest_code.py --repo /data/repo/sample --module demo --commit abc123
本地（连容器化的 PG）:
    uv run python scripts/ingest_code.py --repo ../data/repo/sample --module demo
可选 --small-file-lines 0：强制方法级切片（默认 <200 行按整文件切片）。
M46 起默认构建关联（锚点 + 跨类调用图）；--no-relations 跳过（旧默认行为）。

实现：薄封装 app.pipeline.ingest.ingest_repo（exts={".java":"code"}），不再内联遍历/提交逻辑。
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
    ap = argparse.ArgumentParser(description="Ingest a Java repo into PostgreSQL.")
    ap.add_argument("--repo", required=True, help="仓库根目录")
    ap.add_argument("--module", default=None, help="模块名（不传则按包名首段）")
    ap.add_argument("--commit", default="UNKNOWN", help="git commit hash")
    ap.add_argument("--small-file-lines", type=int, default=None,
                    help="覆盖小文件阈值（0=强制方法级切片）")
    ap.add_argument("--no-relations", action="store_true",
                    help="跳过关联构建（锚点 + 调用图）；默认构建（M46）")
    ap.add_argument("--ext", default=".java", help="文件扩展名（默认 .java）")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    if not repo.exists():
        print(f"repo not found: {repo}", file=sys.stderr)
        return 2

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        stats = ingest_repo(
            session, repo, module=args.module, commit_hash=args.commit,
            small_file_lines=args.small_file_lines, build_relations=not args.no_relations,
            exts={args.ext: "code"},
        )
        session.commit()

    total_files = total_chunks = 0
    for d in stats["details"]:
        total_files += 1
        total_chunks += d.get("chunks", 0)
        print(f"  + {d['file_path']}: classes={d.get('classes')} "
              f"chunks={d.get('chunks')} (method={d.get('method_chunks')})")
    for err in stats["errors"]:
        print(f"  ! {err['file']}: {err['error']}", file=sys.stderr)

    print(f"DONE. files={total_files} chunks={total_chunks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
