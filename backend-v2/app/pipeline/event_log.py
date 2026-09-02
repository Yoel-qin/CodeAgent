"""pipeline_events 账本读写（Task 12）——webhook/worker 共用的幂等闸门。

**sync Session 铁律**：本模块面向 CLI / worker 侧的同步会话（与 ingest_* 脚本同款），
不经 asyncio；API 请求侧（async）只 enqueue、不记账。

幂等契约（唯一键 ``uk_pipeline_events_repo_commit_hash_path``）：

- :func:`record_event` —— ``INSERT ... ON CONFLICT DO NOTHING``。返回 True = 应处理
  （新事件本次落行，status=PENDING；或既有行还是 PENDING = 上次没跑完，续跑）；
  返回 False = 既有行已 DONE（重复消费，调用方直接跳过）。
- :func:`mark_done` / :func:`mark_dead` —— 只 UPDATE（status / attempts / last_error），
  **不 commit**：事务边界归调用方（worker runner 决定一次提交覆盖多少事件）。
  attempts 自增；mark_dead 顺带把 error 落 last_error，mark_done 清空 last_error。
"""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.pipeline import PipelineEvent

_DONE = "DONE"


def record_event(
    session: Session, *, repo: str, commit_hash: str, path: str, event_kind: str
) -> bool:
    """记账一事件；返回 True = 需要处理，False = 已 DONE 的重复（跳过）。"""
    result = session.execute(
        pg_insert(PipelineEvent)
        .values(repo=repo, commit_hash=commit_hash, path=path, event_kind=event_kind)
        .on_conflict_do_nothing(index_elements=["repo", "commit_hash", "path"])
    )
    if (result.rowcount or 0) == 1:
        return True  # 新事件，本次 INSERT 生效（status=PENDING）
    existing = session.execute(
        select(PipelineEvent.status).where(
            PipelineEvent.repo == repo,
            PipelineEvent.commit_hash == commit_hash,
            PipelineEvent.path == path,
        )
    ).scalar_one_or_none()
    return existing != _DONE  # PENDING → True（续跑）；DONE → False；行没了 → True（重来）


def mark_done(session: Session, *, repo: str, commit_hash: str, path: str) -> None:
    """置 DONE + attempts+1 + 清 last_error。不 commit——调用方控制事务边界。"""
    session.execute(
        update(PipelineEvent)
        .where(
            PipelineEvent.repo == repo,
            PipelineEvent.commit_hash == commit_hash,
            PipelineEvent.path == path,
        )
        .values(status=_DONE, attempts=PipelineEvent.attempts + 1, last_error=None)
    )


def mark_dead(
    session: Session, *, repo: str, commit_hash: str, path: str, error: str | None = None
) -> None:
    """置 DEAD + attempts+1 + 落 last_error。不 commit——调用方控制事务边界。"""
    session.execute(
        update(PipelineEvent)
        .where(
            PipelineEvent.repo == repo,
            PipelineEvent.commit_hash == commit_hash,
            PipelineEvent.path == path,
        )
        .values(status="DEAD", attempts=PipelineEvent.attempts + 1, last_error=error)
    )
