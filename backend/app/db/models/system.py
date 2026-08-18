"""系统表：retrieval_logs（LTR 训练 + 效果评估）/ ranking_model_config（§10.13-14）。"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RetrievalLog(Base):
    """检索日志（用于 LTR 训练与效果评估）。"""
    __tablename__ = "retrieval_logs"

    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    recall_results: Mapped[dict] = mapped_column(JSONB, nullable=False)
    recall_count: Mapped[int | None] = mapped_column(Integer)
    coarse_rank_results: Mapped[dict | None] = mapped_column(JSONB)
    coarse_rank_count: Mapped[int | None] = mapped_column(Integer)
    fine_rank_results: Mapped[dict | None] = mapped_column(JSONB)
    fine_rank_count: Mapped[int | None] = mapped_column(Integer)
    final_chunk_ids: Mapped[dict | None] = mapped_column(JSONB)
    # Agent 工具调用轨迹（mode:agent 路径）：[{tool, args, n}, ...]；legacy/retrieve 路径为 NULL。
    agent_steps: Mapped[list | None] = mapped_column(JSONB)
    user_feedback: Mapped[str | None] = mapped_column(String(32))  # HELPFUL/NOT_HELPFUL
    feedback_time: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    # M43 反馈闭环：负反馈的分类（JSONB 数组，中文字面量）+ 纠错文本；旧列不动（monitor/agent_stats 口径不变）。
    feedback_categories: Mapped[list | None] = mapped_column(JSONB)
    feedback_correction: Mapped[str | None] = mapped_column(Text)
    recall_latency_ms: Mapped[int | None] = mapped_column(Integer)
    coarse_rank_ms: Mapped[int | None] = mapped_column(Integer)
    fine_rank_ms: Mapped[int | None] = mapped_column(Integer)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_retrieval_logs_time", "created_at"),
        Index("idx_retrieval_logs_feedback", "user_feedback", postgresql_where="user_feedback IS NOT NULL"),
    )


class RankingModelConfig(Base):
    """精排模型配置（融合权重、阈值）。"""
    __tablename__ = "ranking_model_config"

    config_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False)
    semantic_weight: Mapped[float] = mapped_column(Float, default=0.5)
    graph_weight: Mapped[float] = mapped_column(Float, default=0.2)
    structural_weight: Mapped[float] = mapped_column(Float, default=0.3)
    min_score_threshold: Mapped[float] = mapped_column(Float, default=0.3)
    top_k: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
