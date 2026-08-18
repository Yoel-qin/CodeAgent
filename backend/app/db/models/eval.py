"""检索评测运行记录（横切·评测 / Phase 9 评测产品化 M27）。

每次 ``POST /v1/eval/run``（或未来 CLI）跑一轮评测就落一行，持久化 aggregate 指标 +
per_query 明细，供前端 EvalPage 历史/趋势。镜像 ``history.SyncTask`` 的 job-history 形态：
``status`` 为 ``String(32)`` 无 DB enum（新增状态值零迁移）。
"""
from __future__ import annotations

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EvalRun(Base):
    """单次评测运行的快照（COMPLETED / FAILED）。

    - ``aggregate``：``metrics.aggregate`` 形状（recall/precision/ndcg 按 K + mrr + n）。
      注意 K-map 经 JSONB 序列化后 key 为字符串（``{"1":..,"10":..}``）。
    - ``per_query``：逐 query 明细（重，列表端点不返，仅 ``GET /runs/{id}``）。
    - ``unresolved``：relevant 标注解析失败的 query（不计入 aggregate 分母）。
    - ``embedding_strategy`` 运行时从 ``settings.embedding_strategy`` 盖戳，便于跨策略趋势对比。
    """

    __tablename__ = "eval_runs"

    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING / COMPLETED / FAILED
    trigger: Mapped[str] = mapped_column(String(32), default="api")  # api | cli | manual
    top_k: Mapped[int] = mapped_column(Integer, default=10)
    rewrite: Mapped[str] = mapped_column(String(16), default="off")  # off | auto
    embedding_strategy: Mapped[str] = mapped_column(String(32), default="unified")
    n_queries: Mapped[int] = mapped_column(Integer, default=0)
    n_evaluable: Mapped[int] = mapped_column(Integer, default=0)
    rerank_on_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    aggregate: Mapped[dict | None] = mapped_column(JSONB)
    config: Mapped[dict | None] = mapped_column(JSONB)
    per_query: Mapped[list | None] = mapped_column(JSONB)
    unresolved: Mapped[list | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_eval_runs_created", "created_at"),
        Index("idx_eval_runs_strategy", "embedding_strategy"),
    )


class CandidateEvalQuery(Base):
    """负反馈自动入集的候选 eval query（M43 反馈闭环）。

    入集门槛见 ``feedback_service``（NOT_HELPFUL 且 [答案错误|内容编造] 或有纠错文本）。
    ``source_message_id`` 唯一 = 幂等键（同 message 重复反馈不重复入集）；``status``
    为 String(32) 无 DB enum：CANDIDATE → EXPORTED（CLI 导出）/ MERGED / REJECTED（人工）。
    服务端**不**自动写 committed 的 eval_set*.yaml——人工审后经 ``scripts/export_candidates.py``
    导出片段再并入。
    """

    __tablename__ = "candidate_eval_queries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)          # 取自 retrieval_logs.query_text
    categories: Mapped[list | None] = mapped_column(JSONB)            # 触发入集的分类快照
    correction: Mapped[str | None] = mapped_column(Text)              # 用户纠错文本快照
    source_message_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("chat_messages.message_id"), nullable=False
    )
    retrieval_log_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("retrieval_logs.log_id")
    )
    repo: Mapped[str | None] = mapped_column(String(256))             # Conversation.target_repo（可空）
    status: Mapped[str] = mapped_column(String(32), default="CANDIDATE", server_default="CANDIDATE")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source_message_id", name="uk_candidate_eval_queries_source_message"),
        Index("idx_candidate_eval_queries_status", "status"),
    )
