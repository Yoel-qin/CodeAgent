"""diag_service 单测:mock generate_fn + judge,无真实检索/LLM/DB。"""
from __future__ import annotations

import pytest

from app.eval.diag_service import (
    DIAG_DIMS,
    DiagQuery,
    aggregate_diag,
    build_rubric,
    load_diag_queries,
    run_diag_eval,
)


class _StubJudge:
    """固定满分(诊断 4 维全 high_good → 全 1.0)。"""

    async def judge(self, question, answer, context, citations, *, rubric, scoring_hints=None):
        from app.eval.judge import DimensionScore, JudgeResult

        scores = {k: DimensionScore(1.0, cfg["weight"]) for k, cfg in rubric.items()}
        return JudgeResult(scores=scores, rationale="ok", raw="")


class _ScoredJudge:
    """按维度名返回固定分,验证逐 query rubric 加权。"""

    async def judge(self, question, answer, context, citations, *, rubric, scoring_hints=None):
        from app.eval.judge import DimensionScore, JudgeResult

        fixed = {"root_cause": 0.5, "code_ref": 1.0, "config_advice": 1.0, "reasoning": 1.0}
        scores = {k: DimensionScore(fixed.get(k, 0.0), cfg["weight"]) for k, cfg in rubric.items()}
        return JudgeResult(scores=scores, rationale="ok", raw="")


async def _fake_generate(session, query, *, top_k, rewrite="off"):
    return ("这是诊断回答。", [{"chunk_id": "code_x", "label": "X.foo"}], {"rerank_on": False})


def test_build_rubric_overrides_zero_weight_and_unknown():
    r = build_rubric({"root_cause": 0.5, "config_advice": 0.0, "bogus": 0.9})
    assert set(r) == {"root_cause", "config_advice"}  # 未知维度忽略
    assert r["root_cause"]["weight"] == 0.5 and r["config_advice"]["weight"] == 0.0  # 0 权重保留
    assert r["root_cause"]["direction"] == "high_good" and "desc" in r["root_cause"]


def test_load_diag_queries_rubric_fallback(tmp_path):
    p = tmp_path / "diag.yaml"
    p.write_text(
        "version: 1\n"
        "rubric_weights_default: {root_cause: 0.4, code_ref: 0.3, config_advice: 0.2, reasoning: 0.1}\n"
        "queries:\n"
        "  - id: d01\n    text: Q1\n    intent: diagnose\n"
        "    expected: {root_cause_hints: [堆积], relevant_code: [Foo], config_suggestions: []}\n"
        "  - id: d02\n    text: Q2\n"
        "    rubric: {root_cause: 1.0}\n",
        encoding="utf-8",
    )
    qs = load_diag_queries(str(p))
    assert qs[0].rubric == {"root_cause": 0.4, "code_ref": 0.3, "config_advice": 0.2, "reasoning": 0.1}
    assert qs[0].expected["root_cause_hints"] == ["堆积"]
    assert qs[1].rubric == {"root_cause": 1.0, "code_ref": 0.3, "config_advice": 0.2, "reasoning": 0.1}  # default 被 query 覆盖
    assert qs[1].intent == "diagnose"  # 缺省


@pytest.mark.asyncio
async def test_run_diag_eval_shapes():
    q = DiagQuery(id="d01", text="Q1", intent="diagnose",
                  expected={"root_cause_hints": ["堆积"]},
                  rubric={"root_cause": 0.4, "code_ref": 0.3, "config_advice": 0.2, "reasoning": 0.1})
    rep = await run_diag_eval(None, [q], generate_fn=_fake_generate, judge=_StubJudge())
    assert rep.n_evaluable == 1 and rep.n_queries == 1
    pq = rep.per_query[0]
    assert pq["id"] == "d01" and pq["intent"] == "diagnose"
    assert pq["answer"] == "这是诊断回答。" and pq["citations_n"] == 1
    assert pq["judge_scores"]["root_cause"] == 1.0
    assert pq["weighted_score"] == 1.0
    assert rep.aggregate["overall"] == 1.0


@pytest.mark.asyncio
async def test_run_diag_eval_weighted_uses_own_rubric():
    """weighted 按该 query 自己的 rubric:root_cause 权重 1.0 其余 0 → weighted=root_cause 分 0.5。"""
    q = DiagQuery(id="d03", text="Q", intent="diagnose", expected={},
                  rubric={"root_cause": 1.0, "code_ref": 0.0, "config_advice": 0.0, "reasoning": 0.0})
    rep = await run_diag_eval(None, [q], generate_fn=_fake_generate, judge=_ScoredJudge())
    assert rep.per_query[0]["weighted_score"] == 0.5


@pytest.mark.asyncio
async def test_run_diag_eval_generate_failure_isolated():
    async def _boom(s, q, *, top_k, rewrite="off"):
        raise RuntimeError("gen boom")

    q = DiagQuery(id="dx", text="Q", intent="diagnose", expected={},
                  rubric={"root_cause": 0.4, "code_ref": 0.3, "config_advice": 0.2, "reasoning": 0.1})
    rep = await run_diag_eval(None, [q], generate_fn=_boom, judge=_StubJudge())
    assert rep.n_evaluable == 0
    assert "gen boom" in rep.per_query[0]["error"]
    assert all(v is None for v in rep.per_query[0]["judge_scores"].values())


@pytest.mark.asyncio
async def test_run_diag_eval_judge_failure_isolated():
    class _FailingJudge:
        async def judge(self, *a, **kw):
            raise RuntimeError("judge oops")

    q = DiagQuery(id="dy", text="Q", intent="diagnose", expected={},
                  rubric={"root_cause": 0.4, "code_ref": 0.3, "config_advice": 0.2, "reasoning": 0.1})
    rep = await run_diag_eval(None, [q], generate_fn=_fake_generate, judge=_FailingJudge())
    assert rep.n_evaluable == 0
    assert "judge oops" in rep.per_query[0]["error"]


def test_aggregate_diag_means_and_overall():
    per_query = [
        {"judge_scores": {"root_cause": 1.0, "code_ref": 0.5, "config_advice": None, "reasoning": 1.0},
         "weighted_score": 0.8},
        {"judge_scores": {"root_cause": 0.5, "code_ref": 0.5, "config_advice": None, "reasoning": None},
         "weighted_score": 0.6},
    ]
    agg = aggregate_diag(per_query)
    assert agg["n"] == 2
    assert agg["means"]["root_cause"] == 0.75
    assert agg["means"]["code_ref"] == 0.5
    assert agg["means"]["config_advice"] is None
    assert agg["means"]["reasoning"] == 1.0  # 仅 1 条非 None → 均值即该值
    assert agg["overall"] == 0.7  # per-query weighted 均值


def test_aggregate_diag_empty():
    agg = aggregate_diag([])
    assert agg == {"n": 0, "means": {d: None for d in DIAG_DIMS}, "overall": None}
