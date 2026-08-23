"""评测运行编排单测（services/eval_run_service.py）。

无真实检索/DB：monkeypatch ``run_eval`` 返 canned ``EvalReport``（``persist=False`` 不写库），
验证 COMPLETED 字段映射 + aggregate 规整为字符串 key + FAILED 路径；list_runs/get_run 用假 session。
"""
from __future__ import annotations

from datetime import UTC, datetime

import app.services.eval_run_service as svc
from app.db.models.eval import EvalRun
from app.eval.eval_service import EvalReport


def _canned_report() -> EvalReport:
    return EvalReport(
        config={"top_k": 10, "rewrite": "off"},
        # 注意：metrics.aggregate 用 int key（{1:..,10:..}），验证 _normalize_agg 转字符串
        aggregate={
            "n": 2,
            "recall": {1: 1.0, 3: 1.0, 5: 1.0, 10: 1.0},
            "precision": {1: 1.0, 3: 0.5, 5: 0.4, 10: 0.2},
            "mrr": 0.75,
            "ndcg": {1: 1.0, 3: 0.9, 5: 0.85, 10: 0.8},
        },
        n_queries=82,
        n_evaluable=2,
        rerank_on_count=1,
        per_query=[{"id": "a01", "text": "x", "rerank_on": True}],
        unresolved=[],
    )


# ---- _normalize_agg：int key → string key ----


def test_normalize_agg_stringifies_keys():
    agg = svc._normalize_agg(_canned_report().aggregate)
    assert agg["recall"] == {"1": 1.0, "3": 1.0, "5": 1.0, "10": 1.0}
    assert agg["ndcg"]["10"] == 0.8
    assert agg["mrr"] == 0.75
    assert svc._normalize_agg(None) is None


# ---- run_and_persist: COMPLETED（persist=False，无 DB）----


async def test_run_and_persist_completed(monkeypatch):
    report = _canned_report()

    async def fake_run_eval(*a, **kw):
        return report

    monkeypatch.setattr(svc, "run_eval", fake_run_eval)
    run = await svc.run_and_persist(None, top_k=10, rewrite="off", persist=False)

    assert run.status == "COMPLETED"
    assert run.n_evaluable == 2 and run.rerank_on_count == 1
    assert run.per_query == report.per_query
    # aggregate 规整为字符串 key（int key → str key）
    assert run.aggregate["recall"] == {"1": 1.0, "3": 1.0, "5": 1.0, "10": 1.0}
    assert run.aggregate["ndcg"]["10"] == 0.8
    # config 合并了运行参数 + report.config + embedding_strategy
    assert run.config["top_k"] == 10 and run.config["rewrite"] == "off"
    assert "eval_set" in run.config and "embedding_strategy" in run.config
    assert run.duration_ms is not None and run.duration_ms >= 0
    assert run.started_at and run.completed_at


# ---- run_and_persist: FAILED（run_eval 抛错 → 翻 FAILED，不中断）----


async def test_run_and_persist_failed(monkeypatch):
    async def boom(*a, **kw):
        raise ValueError("kaboom")

    monkeypatch.setattr(svc, "run_eval", boom)
    run = await svc.run_and_persist(None, top_k=10, rewrite="off", persist=False)

    assert run.status == "FAILED"
    assert "ValueError" in (run.error_message or "")
    assert run.aggregate is None        # 失败不填 aggregate
    assert run.duration_ms is not None and run.duration_ms >= 0


# ---- run_and_persist: ablation 注入 recall_fn（M29）----


async def test_run_and_persist_ablation_injects_recall_fn(monkeypatch):
    """ablation 非 None → 经 _make_recall_fn 构造固定消融 recall_fn 注入 run_eval。"""
    report = _canned_report()
    captured = {}

    async def fake_run_eval(*a, **kw):
        captured["recall_fn"] = kw.get("recall_fn")
        return report

    monkeypatch.setattr(svc, "run_eval", fake_run_eval)
    run = await svc.run_and_persist(None, top_k=10, rewrite="off",
                                    ablation={"rerank": False}, persist=False)

    assert run.status == "COMPLETED"
    recall_fn = captured["recall_fn"]
    assert recall_fn is not None
    # _make_recall_fn 给 _recall.ablation 赋值（ab_service.py:93）→ 断言注入了关精排的 AblationConfig
    assert recall_fn.ablation.rerank is False
    assert run.config["ablation"] == {"rerank": False}    # 落 config


async def test_run_and_persist_no_ablation_keeps_default(monkeypatch):
    """ablation=None → recall_fn=None（run_eval 用默认生产链路），逐字同 M27。"""
    report = _canned_report()
    captured = {}

    async def fake_run_eval(*a, **kw):
        captured["recall_fn"] = kw.get("recall_fn")
        return report

    monkeypatch.setattr(svc, "run_eval", fake_run_eval)
    run = await svc.run_and_persist(None, top_k=10, rewrite="off", persist=False)
    assert captured["recall_fn"] is None
    assert "ablation" not in (run.config or {})   # 不落 ablation 键


def test_build_ablation_recall_fn_unknown_field_raises():
    """未知 ablation 字段 → ValueError（API 层转 422）。"""
    import pytest

    with pytest.raises(ValueError):
        svc._build_ablation_recall_fn({"bogus": True})


# ---- list_runs / get_run：假 session（无 DB）----


class _Scalars:
    def __init__(self, rows): self._rows = rows

    def all(self): return self._rows


class _Result:
    def __init__(self, rows): self._rows = rows

    def scalars(self): return _Scalars(self._rows)


class _FakeSession:
    def __init__(self, rows): self._rows = rows

    async def execute(self, stmt): return _Result(self._rows)

    async def get(self, model, pk):
        return next((r for r in self._rows if r.run_id == pk), None)


def _row(rid: int) -> EvalRun:
    return EvalRun(
        run_id=rid, status="COMPLETED", trigger="api", top_k=10, rewrite="off",
        embedding_strategy="unified", n_queries=82, n_evaluable=80, rerank_on_count=80,
        duration_ms=1000, aggregate={"n": 80, "recall": {"10": 0.9}, "precision": {"10": 0.5},
                                     "mrr": 0.8, "ndcg": {"10": 0.85}},
        created_at=datetime.now(UTC),
    )


async def test_list_runs_and_get_run():
    session = _FakeSession([_row(2), _row(1)])  # 假装已 desc 排序
    runs = await svc.list_runs(session, limit=50)
    assert [r.run_id for r in runs] == [2, 1]
    assert await svc.get_run(session, 1) is not None
    assert await svc.get_run(session, 999) is None


# ---- run_qa_and_persist（M39，kind="qa"）----


async def test_run_qa_and_persist_completed(monkeypatch):
    from app.eval.qa_service import QAReport
    report = QAReport(
        config={"top_k": 8, "rewrite": "off"},
        aggregate={"n": 1, "means": {"faithfulness": 0.9, "unverified_rate": 0.1},
                   "weighted_quality": 0.85},
        n_queries=2, n_evaluable=1,
        per_query=[{"id": "q01", "judge_scores": {"faithfulness": 0.9}, "unverified_rate": 0.1}],
        unresolved=[],
    )

    async def fake_run_qa(*a, **kw):
        return report

    monkeypatch.setattr(svc, "run_qa_eval", fake_run_qa)
    monkeypatch.setattr(svc, "load_qa_queries", lambda p: ["q"])
    run = await svc.run_qa_and_persist(None, top_k=8, persist=False)

    assert run.status == "COMPLETED"
    assert run.config["kind"] == "qa"
    assert run.aggregate["means"]["faithfulness"] == 0.9
    assert run.per_query == report.per_query
    assert "rubric_weights" in run.config


async def test_run_qa_and_persist_failed(monkeypatch):
    async def boom(*a, **kw):
        raise ValueError("kaboom")

    monkeypatch.setattr(svc, "run_qa_eval", boom)
    monkeypatch.setattr(svc, "load_qa_queries", lambda p: ["q"])
    run = await svc.run_qa_and_persist(None, persist=False)
    assert run.status == "FAILED"
    assert run.aggregate is None


# ---- run_diag_and_persist（M40，kind="diagnosis"）----


async def test_run_diag_and_persist_completed(monkeypatch):
    from app.eval.diag_service import DiagReport
    report = DiagReport(
        config={"top_k": 8, "rewrite": "off"},
        aggregate={"n": 2, "means": {"root_cause": 0.85, "code_ref": 0.7,
                                     "config_advice": 0.6, "reasoning": 0.9},
                   "overall": 0.78},
        n_queries=2, n_evaluable=2,
        per_query=[{"id": "d01", "judge_scores": {"root_cause": 0.85}, "weighted_score": 0.8}],
    )

    async def fake_run_diag(*a, **kw):
        return report

    monkeypatch.setattr(svc, "run_diag_eval", fake_run_diag)
    monkeypatch.setattr(svc, "load_diag_queries", lambda p: ["q"])
    run = await svc.run_diag_and_persist(None, top_k=8, persist=False)

    assert run.status == "COMPLETED"
    assert run.config["kind"] == "diagnosis"
    assert run.aggregate["means"]["root_cause"] == 0.85
    assert run.aggregate["overall"] == 0.78
    assert run.per_query == report.per_query
    assert run.rerank_on_count == 0


async def test_run_diag_and_persist_failed(monkeypatch):
    async def boom(*a, **kw):
        raise ValueError("kaboom")

    monkeypatch.setattr(svc, "run_diag_eval", boom)
    monkeypatch.setattr(svc, "load_diag_queries", lambda p: ["q"])
    run = await svc.run_diag_and_persist(None, persist=False)
    assert run.status == "FAILED"
    assert run.aggregate is None
    assert "kaboom" in run.error_message


# ---- run_and_persist: tags 子集过滤（M31）----


async def test_run_and_persist_tags_filters_queries(monkeypatch):
    """tags 非 None → 只保留 tag 交集非空的 query；n_queries/config["tags"] 同步。"""
    from app.eval.eval_service import EvalQuery
    report = _canned_report()
    captured = {}

    async def fake_run_eval(session, queries, **kw):
        captured["ids"] = [q.id for q in queries]
        return report

    monkeypatch.setattr(svc, "run_eval", fake_run_eval)
    monkeypatch.setattr(svc, "load_eval_queries", lambda p: [
        EvalQuery(id="rm01", text="甲", relevant=["A.a"], tags=["rocketmq"]),
        EvalQuery(id="a01", text="乙", relevant=["B.b"]),
        EvalQuery(id="rm02", text="丙", relevant=["C.c"], tags=["rocketmq", "other"]),
    ])
    run = await svc.run_and_persist(None, tags=["rocketmq"], persist=False)

    assert captured["ids"] == ["rm01", "rm02"]
    assert run.n_queries == 2
    assert run.config["tags"] == ["rocketmq"]


async def test_run_and_persist_tags_empty_filter_raises(monkeypatch):
    """tags 过滤后无 query → ValueError（配置错误，不落 FAILED 行）。"""
    import pytest

    from app.eval.eval_service import EvalQuery

    monkeypatch.setattr(svc, "load_eval_queries", lambda p: [
        EvalQuery(id="a01", text="乙", relevant=["B.b"]),
    ])
    with pytest.raises(ValueError):
        await svc.run_and_persist(None, tags=["rocketmq"], persist=False)
