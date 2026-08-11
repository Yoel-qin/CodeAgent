"""检索评测运行记录（横切·评测 / Phase 9 评测产品化 M27）。

每次 ``POST /v1/eval/run``（或未来 CLI）跑一轮评测就落一行，持久化 aggregate 指标 +
per_query 明细，供前端 EvalPage 历史/趋势。镜像 ``history.SyncTask`` 的 job-history 形态：
``status`` 为 ``String(32)`` 无 DB enum（新增状态值零迁移）。
"""
from __future__ import annotations

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func
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
