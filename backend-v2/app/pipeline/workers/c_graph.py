"""Worker C（Task 13）：graph_rebuild 事件 → 全量重建实体/调用边/度量。

复用 CLI 抽出的 :func:`run_full_code_ingest`（parse → upsert entities →
replace_edges → metrics），与 ``scripts/ingest_code.py`` 同一实现。

P2 优化 = 增量边作用域（只重建受影响文件牵连的边）；v1 全量重建——文件级增量已由
Worker B 承担（A/M 删旧重建、D 清理），图重建作兜底校准，幂等（replace_edges 按
repo 先删后插 + metrics ON CONFLICT entity_id）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.pipeline.ingest_code import run_full_code_ingest
from app.pipeline.workers import WorkerError, repo_dir_of


def rebuild_graph(session: Session, *, repo: str) -> dict:
    """全量重建该 repo 的实体/调用边/度量（不 commit——runner 控制事务边界）。"""
    repo_dir = repo_dir_of(repo)
    if not repo_dir.is_dir():
        raise WorkerError(f"repo 目录不存在: {repo_dir}")
    return run_full_code_ingest(session, repo=repo, repo_dir=repo_dir)
