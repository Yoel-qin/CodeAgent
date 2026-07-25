"""变更历史与同步任务：change_history / sync_tasks / rollback_history（§10 + §18 回滚）。"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChangeHistory(Base):
    """变更历史（含回滚字段 §18）。
    change_type: ADDED / MODIFIED / DELETED + §18: ROLLBACK / RESTORED
    """
    __tablename__ = "change_history"

    history_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chunk_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(32), nullable=False)
    change_type: Mapped[str] = mapped_column(String(16), nullable=False)
    old_content_hash: Mapped[str | None] = mapped_column(String(64))
    new_content_hash: Mapped[str | None] = mapped_column(String(64))
    old_content: Mapped[str | None] = mapped_column(Text)
    new_content: Mapped[str | None] = mapped_column(Text)
    git_commit_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    git_commit_time: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    git_author: Mapped[str | None] = mapped_column(String(128))
    commit_message: Mapped[str | None] = mapped_column(String(512))
    affected_relations: Mapped[int] = mapped_column(Integer, default=0)
    # §18 回滚字段
    rollback_source_commit: Mapped[str | None] = mapped_column(String(40))
    is_rollback_related: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_history_chunk", "chunk_id"),
        Index("idx_history_commit", "git_commit_hash"),
        Index("idx_history_type", "change_type"),
        Index("idx_history_time", "git_commit_time", postgresql_using="btree"),
        Index("idx_history_rollback", "rollback_source_commit", postgresql_where="rollback_source_commit IS NOT NULL"),
    )


class SyncTask(Base):
    """增量同步任务（含文档 PR 字段 §18）。"""
    __tablename__ = "sync_tasks"

    task_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    commit_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    chunks_added: Mapped[int] = mapped_column(Integer, default=0)
    chunks_modified: Mapped[int] = mapped_column(Integer, default=0)
    chunks_deleted: Mapped[int] = mapped_column(Integer, default=0)
    relations_updated: Mapped[int] = mapped_column(Integer, default=0)
    vector_sync_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    graph_update_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    # §18 文档 PR 字段
    doc_pr_url: Mapped[str | None] = mapped_column(String(512))
    doc_pr_status: Mapped[str | None] = mapped_column(String(32))  # OPEN/MERGED/CLOSED_BY_ROLLBACK
    change_details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_sync_tasks_status", "status"),
        Index("idx_sync_tasks_commit", "commit_hash"),
    )


class RollbackHistory(Base):
    """回滚记录 §18.2.1。"""
    __tablename__ = "rollback_history"

    rollback_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rollback_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    source_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    chunks_rolled_back: Mapped[int] = mapped_column(Integer, default=0)
    chunks_restored: Mapped[int] = mapped_column(Integer, default=0)
    chunks_deleted: Mapped[int] = mapped_column(Integer, default=0)
    relations_restored: Mapped[int] = mapped_column(Integer, default=0)
    anchors_restored: Mapped[int] = mapped_column(Integer, default=0)
    stale_anchors_cleared: Mapped[int] = mapped_column(Integer, default=0)
    doc_pr_closed: Mapped[str | None] = mapped_column(String(512))
    triggered_by: Mapped[str] = mapped_column(String(32), default="MANUAL")  # MANUAL/AGENT_SUGGESTED/CI_AUTO
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_rollback_source", "source_commit"),
        Index("idx_rollback_commit", "rollback_commit"),
    )
