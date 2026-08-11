"""检索评测指标单测（eval/metrics.py）。纯函数，无外部依赖（仿 tests/test_fusion.py）。"""
from __future__ import annotations

import pytest

from app.eval.metrics import (
    aggregate,
    evaluate_query,
    first_hit_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

# ---- recall_at_k ----

def test_recall_partial_full_and_miss():
    retrieved = ["a", "b", "c", "d", "e"]
    rel = {"b", "e"}
    assert recall_at_k(retrieved, rel, 5) == 1.0      # 两个都在前 5
    assert recall_at_k(retrieved, rel, 3) == 0.5      # 仅 b 在前 3
    assert recall_at_k(retrieved, rel, 1) == 0.0      # 前 1 无命中


def test_recall_empty_relevant_is_zero():
    assert recall_at_k(["a", "b"], set(), 5) == 0.0


# ---- precision_at_k ----

def test_precision_at_k():
    retrieved = ["a", "b", "c", "d"]
    rel = {"b", "d"}
    assert precision_at_k(retrieved, rel, 4) == 0.5   # 2/4
    assert precision_at_k(retrieved, rel, 2) == 0.5   # [a,b] 命中 b → 1/2


# ---- reciprocal_rank (MRR per query) ----

def test_reciprocal_rank_first_second_and_none():
    assert reciprocal_rank(["a", "b", "c"], {"a"}) == 1.0
    assert reciprocal_rank(["a", "b", "c"], {"b"}) == 0.5
    assert reciprocal_rank(["a", "b", "c"], {"z"}) == 0.0


# ---- ndcg_at_k ----

def test_ndcg_perfect_order_is_one():
    # 相关项在第 1 位 → DCG=IDCG
    assert ndcg_at_k(["b", "a", "c"], {"b"}, 3) == 1.0


def test_ndcg_single_relevant_at_rank_two():
    # DCG = 1/log2(3) ≈ 0.63093；IDCG（理想第 1 位）= 1/log2(2) = 1.0
    assert ndcg_at_k(["a", "b", "c"], {"b"}, 3) == pytest.approx(0.6309, abs=1e-4)


def test_ndcg_multiple_relevant():
    # DCG = 1/log2(3)+1/log2(5) ≈ 1.06161；IDCG = 1/log2(2)+1/log2(3) ≈ 1.63093 → ≈ 0.6509
    assert ndcg_at_k(["a", "b", "c", "d"], {"b", "d"}, 4) == pytest.approx(0.6509, abs=1e-4)


def test_ndcg_no_relevant_or_bad_k_is_zero():
    assert ndcg_at_k(["a", "b"], set(), 3) == 0.0
    assert ndcg_at_k(["a", "b"], {"a"}, 0) == 0.0


# ---- first_hit_rank ----

def test_first_hit_rank():
    assert first_hit_rank(["a", "b", "c"], {"b"}) == 2
    assert first_hit_rank(["a", "b", "c"], {"a"}) == 1
    assert first_hit_rank(["a", "b", "c"], {"z"}) is None


# ---- evaluate_query ----

def test_evaluate_query_structure_and_values():
    res = evaluate_query(["a", "b", "c"], {"b"})
    assert set(res.keys()) == {"recall", "precision", "mrr", "ndcg", "first_hit_rank"}
    assert res["recall"][1] == 0.0 and res["recall"][3] == 1.0
    assert res["mrr"] == 0.5
    assert res["ndcg"][3] == 0.6309
    assert res["first_hit_rank"] == 2


# ---- aggregate ----

def test_aggregate_macro_average():
    # q1: 相关 a 在第 1 位（完美）；q2: 相关 b 在第 2 位
    q1 = evaluate_query(["a", "b"], {"a"})
    q2 = evaluate_query(["a", "b"], {"b"})
    agg = aggregate([q1, q2])
    assert agg["n"] == 2
    assert agg["mrr"] == round((1.0 + 0.5) / 2, 4)   # 0.75
    assert agg["recall"][1] == round((1.0 + 0.0) / 2, 4)   # 0.5
    assert agg["recall"][3] == 1.0                     # 两者 k=3 都全中


def test_aggregate_empty_is_none_metrics():
    agg = aggregate([])
    assert agg["n"] == 0
    assert agg["mrr"] is None
    assert agg["recall"][1] is None
