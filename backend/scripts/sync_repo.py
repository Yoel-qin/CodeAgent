"""CLI：对一个 git 仓库跑增量/全量同步（§13 增量更新 + §18 回滚检测）。

入口 :func:`run_sync`（``app.services.sync_service``）与 API ``POST /v1/sync/trigger`` 共用。
FULL 复用 ``ingest_repo``；INCREMENTAL 用 ``git diff`` 检测变更（无 COMPLETED 游标时自动回退 FULL）。
``run_sync`` 内部已提交事务并维护 ``sync_tasks`` 生命周期，本脚本无需额外 commit。

本地（连容器化 PG）:
    uv run python scripts/sync_repo.py --repo ../data/repo/sample --type INCREMENTAL
    uv run python scripts/sync_repo.py --repo ../data/repo/sample --type FULL --target-commit <sha>

说明：仓库需是 git 仓库且工作区已检出到目标提交（``--target-commit`` 缺省取 HEAD）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 中文 Windows GBK：脚本输出含中文/emoji，强制 stdout/stderr 为 utf-8（CLAUDE.md「中文 Windows」）
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 确保以 `python scripts/xxx.py` 直接运行时能 import app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.sync_service import run_sync


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run an incremental/full git sync on a repo.")
    ap.add_argument("--repo", default=settings.repo_path, help="仓库根目录（默认 settings.repo_path）")
    ap.add_argument("--type", choices=["FULL", "INCREMENTAL"], default="INCREMENTAL", help="同步类型")
    ap.add_argument("--target-commit", default=None, help="同步到该提交（缺省取 HEAD）")
    ap.add_argument("--no-relations", action="store_true",
                    help="FULL 时跳过关联构建（build_all）；对 INCREMENTAL 无影响")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    if not repo.exists():
        print(f"repo not found: {repo}", file=sys.stderr)
        return 2

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        task = run_sync(
            session, repo, type=args.type, target_commit=args.target_commit,
            build_relations=False if args.no_relations else None,
        )

    cd = task.change_details or {}
    print(f"task #{task.task_id} type={cd.get('type')} status={task.status} commit={task.commit_hash[:10]}")
    if task.status == "COMPLETED":
        print(f"  files_changed={task.files_changed} "
              f"added={task.chunks_added} modified={task.chunks_modified} deleted={task.chunks_deleted} "
              f"relations_updated={task.relations_updated} rollbacks={cd.get('rollbacks', 0)}")
        if cd.get("fallback"):
            print(f"  fallback={cd['fallback']} reason={cd.get('reason')}")
        for err in cd.get("errors", []):
            print(f"  ! {err.get('file')}: {err.get('error')}", file=sys.stderr)
    else:
        print(f"  error: {task.error_message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
