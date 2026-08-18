"""M43 反馈闭环模型单测（无 DB）：列存在性 + CandidateEvalQuery 形状 + 命名约定。"""
from __future__ import annotations

from app.db.base import Base
from app.db.models.eval import CandidateEvalQuery
from app.db.models.system import RetrievalLog


def test_retrieval_log_has_feedback_columns():
    cols = RetrievalLog.__table__.c
    assert "feedback_categories" in cols          # JSONB 数组
    assert "feedback_correction" in cols          # Text
    assert cols["feedback_categories"].nullable   # 可空=旧行零迁移兼容
    assert cols["feedback_correction"].nullable


def test_candidate_eval_query_shape():
    cols = CandidateEvalQuery.__table__.c
    for name in ("id", "query", "categories", "correction", "source_message_id",
                 "retrieval_log_id", "repo", "status", "created_at"):
        assert name in cols, name
    # uk 幂等键：同 message 不重复入集
    uks = [c.name for c in CandidateEvalQuery.__table__.constraints
           if c.__class__.__name__ == "UniqueConstraint"]
    assert any("source_message" in (n or "") for n in uks), uks
    # status 默认 CANDIDATE（String(32) 无 enum，惯例）
    assert cols["status"].default.arg == "CANDIDATE"
    # 注册进 Base.metadata（Alembic 可见）
    assert "candidate_eval_queries" in Base.metadata.tables


def test_models_registered_in_package_export():
    from app.db import models as pkg
    assert pkg.CandidateEvalQuery is CandidateEvalQuery
    assert "CandidateEvalQuery" in pkg.__all__
