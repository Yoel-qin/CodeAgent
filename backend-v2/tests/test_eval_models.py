"""Task 1：EvalRun 模型 + 迁移。真 PG（session fixture 连接级回滚，不留痕）。"""
from datetime import datetime

from app.db.models import EvalRun


def test_eval_run_roundtrip_defaults(session):
    run = EvalRun(repo="rocketmq", kind="single", status="RUNNING",
                  config={"trigger": "test", "variants": [{"name": "baseline"}]})
    session.add(run)
    session.flush()
    assert run.id > 0
    assert run.metrics is None and run.per_query is None and run.error is None
    assert run.finished_at is None
    assert isinstance(run.created_at, datetime)
    assert run.created_at.tzinfo is not None  # aware（沿 trace.py 同款 default）


def test_eval_run_status_mutation_no_enum(session):
    """status 是 String 无枚举——任意新状态值直接可写（加状态值永不改表）。"""
    session.add(EvalRun(repo="r", kind="ab", status="PARTIAL", config={}))
    session.flush()  # 不抛 = 通过
