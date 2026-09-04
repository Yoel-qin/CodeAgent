"""评测运行账本 ORM：EvalRun（M8）。

- kind = ``single``（单变体）| ``ab``（多变体对比），discriminator 在 config 同列；
  status = ``RUNNING``|``DONE``|``FAILED``（String 无 DB 枚举，沿 pipeline_events 约定）。
- config 存变体清单与触发来源（api|cli）；metrics 形状冻结为
  ``{"variants": {name: aggregate_dict}, "judge": {...}|None}``；
  per_query 为平铺行列表（每行含 variant 键，前端按 case_id 分组做 A/B 配对明细）。
- 答案全文不进 per_query（只存 answer_chars）——防 JSONB 膨胀；judge 分数进 metrics。
- created_at 补 Python 侧 default（flush 后属性即可用，避免 async 会话惰性加载，
  与 trace.py 同款）；finished_at 在终态时由 service 写入。
"""
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_pg_jsonb = JSON().with_variant(JSONB, "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        Index("ix_eval_runs_repo", "repo"),
        Index("ix_eval_runs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repo: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(16), default="single")  # single|ab
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")  # RUNNING|DONE|FAILED
    config: Mapped[dict] = mapped_column(_pg_jsonb, default=dict)
    metrics: Mapped[dict | None] = mapped_column(_pg_jsonb, nullable=True)
    per_query: Mapped[list | None] = mapped_column(_pg_jsonb, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
