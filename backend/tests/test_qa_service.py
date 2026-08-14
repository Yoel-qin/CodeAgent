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
            scores[k] = DimensionScore(0.0 if cfg["direction"] == "low_bad" else 1.0, cfg["weight"])
        return JudgeResult(scores=scores, rationale="ok", raw="")


async def _fake_generate(session, query, *, top_k, rewrite="off"):
    return ("这是回答。", [{"chunk_id": "code_x", "label": "X.foo"}], {"rerank_on": False})


@pytest.mark.asyncio
async def test_run_qa_eval_shapes():
    queries = [QAQuery(id="q01", text="Q1", scoring_hints={})]
    rep = await run_qa_eval(
        None, queries, top_k=8, rewrite="off",
        generate_fn=_fake_generate, judge=_StubJudge(),
    )
    assert rep.n_evaluable == 1
    pq = rep.per_query[0]
    assert pq["id"] == "q01" and pq["answer"] == "这是回答。"
    assert pq["judge_scores"]["faithfulness"] == 1.0
    assert pq["unverified_rate"] == 0.0  # answer 无标识符 → enforce ratio=0
    assert rep.aggregate["weighted_quality"] == 1.0


def test_aggregate_qa_means_and_weighted():
    per_query = [
        {"judge_scores": {"faithfulness": 1.0, "answer_relevance": 1.0,
                          "citation_accuracy": 1.0, "hallucination": 0.0}, "unverified_rate": 0.0},
        {"judge_scores": {"faithfulness": 0.5, "answer_relevance": 0.5,
                          "citation_accuracy": 0.5, "hallucination": 0.5}, "unverified_rate": 0.5},
    ]
    agg = aggregate_qa(per_query, QA_RUBRIC)
    assert agg["n"] == 2
    assert agg["means"]["faithfulness"] == 0.75
    # hallucination low_bad → (1-0.25)=0.75；4 维等权 → weighted_quality=0.75
    assert agg["weighted_quality"] == 0.75


def test_aggregate_qa_skips_none_dims():
    per_query = [{"judge_scores": {"faithfulness": None, "answer_relevance": 1.0,
                                   "citation_accuracy": 1.0, "hallucination": 0.0}, "unverified_rate": 0.0}]
    agg = aggregate_qa(per_query, QA_RUBRIC)
    assert agg["means"]["faithfulness"] is None
    # weighted_quality 仅聚合非 None 维度
    assert agg["weighted_quality"] is not None
