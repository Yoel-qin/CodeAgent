"""qa_service 单测：mock generate_fn + judge，无真实检索/LLM/DB。"""
from __future__ import annotations

import pytest

from app.eval.judge import QA_RUBRIC
from app.eval.qa_service import QAQuery, aggregate_qa, run_qa_eval


class _StubJudge:
    """固定返回 4 维满分（hallucination low_bad → 0.0 表示无幻觉）。"""

    async def judge(self, question, answer, context, citations, *, rubric):
        from app.eval.judge import DimensionScore, JudgeResult

        scores = {}
        for k, cfg in rubric.items():
            # low_bad 维度：0.0 = 满分（无幻觉/无问题）
            scores[k] = DimensionScore(
                0.0 if cfg["direction"] == "low_bad" else 1.0, cfg["weight"]
            )
        return JudgeResult(scores=scores, rationale="ok", raw="")


class _FailingJudge:
    """模拟无内部容错的 DI judge，抛异常。"""

    async def judge(self, question, answer, context, citations, *, rubric):
        raise RuntimeError("judge oops")


async def _fake_generate(session, query, *, top_k, rewrite="off"):
    return ("这是回答。", [{"chunk_id": "code_x", "label": "X.foo"}], {"rerank_on": False})


@pytest.mark.asyncio
async def test_run_qa_eval_shapes():
    queries = [QAQuery(id="q01", text="Q1", scoring_hints={})]
    rep = await run_qa_eval(
        None,
        queries,
        top_k=8,
        rewrite="off",
        generate_fn=_fake_generate,
        judge=_StubJudge(),
    )
    assert rep.n_evaluable == 1
    pq = rep.per_query[0]
    assert pq["id"] == "q01" and pq["answer"] == "这是回答。"
    assert pq["judge_scores"]["faithfulness"] == 1.0
    assert pq["unverified_rate"] == 0.0  # answer 无标识符 → enforce ratio=0
    assert rep.aggregate["weighted_quality"] == 1.0


@pytest.mark.asyncio
async def test_run_qa_eval_judge_failure_isolated():
    """DI judge 抛异常 → 该 query 维度全 None + error，不中断整轮。"""
    queries = [QAQuery(id="q02", text="Q2", scoring_hints={})]
    rep = await run_qa_eval(
        None,
        queries,
        top_k=8,
        rewrite="off",
        generate_fn=_fake_generate,
        judge=_FailingJudge(),
    )
    assert rep.n_queries == 1
    assert rep.n_evaluable == 0  # judge 失败 → 有 error → 不算 evaluable
    pq = rep.per_query[0]
    assert pq["error"] is not None and "judge oops" in pq["error"]
    assert all(v is None for v in pq["judge_scores"].values())


@pytest.mark.asyncio
async def test_run_qa_eval_n_evaluable_excludes_errors():
    """n_evaluable 只计无 error 的 query（generate 失败 + judge 失败均排除）。"""
    async def _gen_ok(s, q, *, top_k, rewrite="off"):
        return ("ans", [{"chunk_id": "c", "label": "L"}], {})

    async def _gen_fail(s, q, *, top_k, rewrite="off"):
        raise RuntimeError("gen boom")

    results = []

    class _SelectiveJudge:
        async def judge(self, question, answer, context, citations, *, rubric):
            results.append(question)
            if question == "bad-judge":
                raise RuntimeError("judge boom")
            return _StubJudge().judge(question, answer, context, citations, rubric=rubric)

    # Can't easily swap generate_fn per query with a single fn param,
    # so test generate-failure separately:
    rep1 = await run_qa_eval(
        None,
        [QAQuery(id="ok", text="fine", scoring_hints={})],
        generate_fn=_gen_ok,
        judge=_StubJudge(),
    )
    assert rep1.n_evaluable == 1

    rep2 = await run_qa_eval(
        None,
        [QAQuery(id="fail", text="x", scoring_hints={})],
        generate_fn=_gen_fail,
        judge=_StubJudge(),
    )
    assert rep2.n_evaluable == 0
    assert rep2.per_query[0]["error"] is not None

    rep3 = await run_qa_eval(
        None,
        [QAQuery(id="jf", text="bad-judge", scoring_hints={})],
        generate_fn=_gen_ok,
        judge=_SelectiveJudge(),
    )
    assert rep3.n_evaluable == 0
    assert "judge boom" in rep3.per_query[0]["error"]


def test_aggregate_qa_means_and_weighted():
    per_query = [
        {
            "judge_scores": {
                "faithfulness": 1.0,
                "answer_relevance": 1.0,
                "citation_accuracy": 1.0,
                "hallucination": 0.0,
            },
            "unverified_rate": 0.0,
        },
        {
            "judge_scores": {
                "faithfulness": 0.5,
                "answer_relevance": 0.5,
                "citation_accuracy": 0.5,
                "hallucination": 0.5,
            },
            "unverified_rate": 0.5,
        },
    ]
    agg = aggregate_qa(per_query, QA_RUBRIC)
    assert agg["n"] == 2
    assert agg["means"]["faithfulness"] == 0.75
    # hallucination low_bad → (1-0.25)=0.75；4 维等权 → weighted_quality=0.75
    assert agg["weighted_quality"] == 0.75


def test_aggregate_qa_skips_none_dims():
    per_query = [
        {
            "judge_scores": {
                "faithfulness": None,
                "answer_relevance": 1.0,
                "citation_accuracy": 1.0,
                "hallucination": 0.0,
            },
            "unverified_rate": 0.0,
        }
    ]
    agg = aggregate_qa(per_query, QA_RUBRIC)
    assert agg["means"]["faithfulness"] is None
    # weighted_quality 仅聚合非 None 维度
    assert agg["weighted_quality"] is not None
