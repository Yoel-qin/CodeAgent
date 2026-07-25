"""CLI：构建关联关系（锚点匹配 → DOC_TO_CODE/CODE_TO_DOC + anchor_mappings）
与调用图（call_graph）。幂等，全量重建。

用法: uv run python scripts/build_relations.py --repo ../data/repo/sample
（--repo 提供则同时构建调用图；不提供则只建锚点关联）
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
from app.pipeline.relations import build_all


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build chunk relations & call graph.")
    ap.add_argument("--repo", default=None, help="代码仓库根（提供则构建调用图）")
    args = ap.parse_args(argv)

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        stats = build_all(session, repo_path=args.repo)
        session.commit()
    a = stats["anchors"]
    print(f"anchor relations: {a['relations']} (DOC_TO_CODE+CODE_TO_DOC)")
    print(f"anchor_mappings : {a['anchor_mappings']}")
    print(f"unmatched anchors: {a['unmatched_anchors']}")
    if "call_graph" in stats:
        print(f"call_graph edges: {stats['call_graph']['call_edges']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
