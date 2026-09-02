"""管道状态表 ORM：PipelineEvent（M5 Task 12）。

离线管道的幂等账本：webhook/worker 每处理一个单元（file / graph_rebuild）先在此
记一行，靠 ``UNIQUE(repo, commit_hash, path)`` + ``ON CONFLICT DO NOTHING`` 去重——
重投/重复消费不再重复 ingest。

实现说明：
- path 对 graph_rebuild 事件固定 ``"__repo__"``（该事件无单一路径），file 事件为
  变更文件相对 repo 根的路径；两列默认 ``""`` 与 brief 契约一致（Python 侧默认，
  Core INSERT 同样生效）。
- status 无 DB 级枚举（String(16)）——加状态值永不改表（照搬旧库约定）。
- attempts/last_error 由 worker runner 经 event_log.mark_done/mark_dead 维护。
- created_at/updated_at 除 server_default 外补 Python 侧 default/onupdate（与
  chat.Conversation 同款：flush 后属性即可用，避免 async 会话惰性加载）。
"""
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PipelineEvent(Base):
    __tablename__ = "pipeline_events"
    __table_args__ = (
        UniqueConstraint(
            "repo", "commit_hash", "path", name="uk_pipeline_events_repo_commit_hash_path"
        ),
        Index("ix_pipeline_events_repo_status", "repo", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repo: Mapped[str] = mapped_column(String(256))
    commit_hash: Mapped[str] = mapped_column(String(64), default="")  # graph_rebuild 也有
    path: Mapped[str] = mapped_column(String(512), default="")  # graph_rebuild 固定 "__repo__"
    event_kind: Mapped[str] = mapped_column(String(32))  # file | graph_rebuild
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING|DONE|DEAD
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        default=_utcnow,
        onupdate=_utcnow,
    )
