"""变更历史与同步任务：change_history / sync_tasks / rollback_history（§10 + §18 回滚）。"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
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


class DocUpdateProposal(Base):
    """文档更新提案（M15：DOC_MAINTAIN 批准后产出的重写 + PR 工件记录）。

    人工审批通过后，``apply`` 节点据当前代码 LLM 重写过时文档段落，把重写工件写回 MinIO，
    并在此表落一行结构化 PR 提案（分支/commit/diff 载荷，**不执行真实 git**，status=PENDING_PUSH）。
    无 LLM 时仅记录待人工重写（status=PENDING_MANUAL）。
    M21 起：approve（``set_proposal_status``）在写回 KB 后**真执行 git**——隔离 worktree 建分支+提交
    （+可选推送），回填 ``commit_sha``/``pr_url``、状态翻 ``PUSHED``（已推送）/``COMMITTED``（仅本地提交）；
    git 失败翻 ``PUSH_FAILED``（KB 已写回）。``source_commit`` 在 ``create_doc_pr`` 捕获 base 提交，
    供回滚 closer（``doc_pr_service.close_open_doc_pr_for``）匹配关 PR（删分支、翻 ``CLOSED_BY_ROLLBACK``）。
    M17 起：SWEEP 批量重写（``sweep_rewrite_service``）也从此表产 PENDING 行＝审批队列，
    人工 approve→``APPROVED``→（M21 git）``PUSHED``/``COMMITTED``/``PUSH_FAILED`` / reject→``REJECTED``。
    status 值（``String(32)`` 无枚举，新增值零迁移）：PENDING_PUSH / PENDING_MANUAL / FAILED /
    MERGED / CLOSED_BY_ROLLBACK / APPROVED / REJECTED / PUSHED / COMMITTED / PUSH_FAILED。
    """
    __tablename__ = "doc_update_proposals"

    proposal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    file_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("doc_files.file_id"))
    doc_chunk_id: Mapped[str | None] = mapped_column(String(128))
    heading_path: Mapped[dict | None] = mapped_column(JSONB)
    relation_ids: Mapped[dict | None] = mapped_column(JSONB)  # list[int]，触发本提案的锚点 relation_id
    original_text: Mapped[str | None] = mapped_column(Text)
    rewritten_text: Mapped[str | None] = mapped_column(Text)  # None ⇒ 未配置 LLM，待人工重写
    artifact_key: Mapped[str | None] = mapped_column(String(512))  # MinIO 重写工件 key
    branch_name: Mapped[str | None] = mapped_column(String(256))
    commit_message: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="PENDING_PUSH")  # PENDING_PUSH/PENDING_MANUAL/FAILED/MERGED/CLOSED_BY_ROLLBACK/APPROVED/REJECTED/PUSHED/COMMITTED/PUSH_FAILED
    pr_url: Mapped[str | None] = mapped_column(String(512))
    commit_sha: Mapped[str | None] = mapped_column(String(40))  # M21：我们在分支上创建的提交 sha（区别于 source_commit=base）
    error_message: Mapped[str | None] = mapped_column(String(512))
    source_commit: Mapped[str | None] = mapped_column(String(40))  # 预留：回滚关闭 PR 联动
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_doc_proposals_status", "status"),
        Index("idx_doc_proposals_conv", "conversation_id"),
        Index("idx_doc_proposals_file", "file_id"),
    )
