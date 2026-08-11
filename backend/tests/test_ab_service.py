"""A/B 评测编排单测（eval/ab_service.py）。

无 infra：直接测纯函数（``_delta`` / ``_pair_deltas`` / ``filter_by_tag`` / ``AblationConfig``）；
``run_ab`` 编排经 monkeypatch ``run_eval`` 注入 canned ``EvalReport``（按 ``recall_fn.ablation``
区分变体），验证 FULL 去重、per-pair delta、rerank_on 计数、JSON 可序列化。
"""
from __future__ import annotations

import json

import pytest

from app.eval import ab_service
from app.eval.ab_service import (
    V_FULL,
    V_NO_GRAPH,
    V_NO_RERANK,
    V_VECTOR_ONLY,
    _delta,
    _make_recall_fn,
    _pair_deltas,
    filter_by_tag,
    run_ab,
)
from app.eval.eval_service import EvalQuery, EvalReport
from app.retrieval.ablation import AblationConfig

_KS = (1, 3, 5, 10)


def _agg(recall: float, precision: float, mrr: float, ndcg: float, n: int = 3) -> dict:
    return {
        "n": n,
        "recall": {k: recall for k in _KS},
        "precision": {k: precision for k in _KS},
        "mrr": mrr,
        "ndcg": {k: ndcg for k in _KS},
    }


# 变体 → (recall, precision, mrr, ndcg) canned 值
_CANNED = {
    "full": (0.70, 0.80, 0.85, 0.90),
    "no_rerank": (0.70, 0.50, 0.60, 0.70),
    "vector_only": (0.50, 0.60, 0.55, 0.60),
    "no_graph": (0.65, 0.78, 0.80, 0.85),
}
_AB_TO_NAME = {v.ablation: v.name for v in (V_FULL, V_NO_RERANK, V_VECTOR_ONLY, V_NO_GRAPH)}


def _queries() -> list[EvalQuery]:
    return [
        EvalQuery(id="q1", text="t1", relevant=["Account.deposit"]),
        EvalQuery(id="q2", text="t2", relevant=["Account.withdraw"]),
        EvalQuery(id="q3", text="t3", relevant=["Foo"], tags=["call_chain"]),
    ]


# ---- 纯函数 ----

def test_ablation_defaults_all_true_and_hashable():
    a = AblationConfig()
    assert (a.vector, a.lexical, a.graph, a.rerank) == (True, True, True, True)
    assert AblationConfig() == AblationConfig()           # frozen + 可哈希（可作 dict 键）
    assert hash(AblationConfig()) == hash(AblationConfig())


def test_named_variants_configs():
    assert V_NO_RERANK.ablation == AblationConfig(rerank=False)
    assert V_VECTOR_ONLY.ablation == AblationConfig(lexical=False, graph=False)
    assert V_NO_GRAPH.ablation == AblationConfig(graph=False)


def test_delta_basic_zero_baseline_and_none():
    assert _delta(0.5, 0.8) == {"abs": 0.3, "pct": 60.0}
    assert _delta(0.0, 0.5) == {"abs": 0.5, "pct": None}  # baseline=0 → pct None
    assert _delta(None, 0.5) == {"abs": None, "pct": None}
    assert _delta(0.5, None) == {"abs": None, "pct": None}


def test_pair_deltas_shape_and_values():
    d = _pair_deltas(_agg(0.5, 0.5, 0.5, 0.5), _agg(0.7, 0.8, 0.85, 0.9))
    assert set(d) == {"recall", "precision", "ndcg", "mrr"}
    assert d["precision"][1]["pct"] == 60.0      # (0.8-0.5)/0.5
    assert d["mrr"]["pct"] == 70.0               # (0.85-0.5)/0.5
    for metric in ("recall", "precision", "ndcg"):
        assert set(d[metric]) == set(_KS)


def test_filter_by_tag():
    qs = _queries()
    assert len(filter_by_tag(qs, "call_chain")) == 1
    assert filter_by_tag(qs, "nope") == []


# ---- run_ab 编排（monkeypatch run_eval）----

async def test_make_recall_fn_forwards_ablation(monkeypatch):
    recorded = {}

    async def fake_recall(session, query, *, top_k, **kw):
        recorded["ablation"] = kw.get("ablation")
        recorded["top_k"] = top_k
        recorded["sem"] = kw.get("semantic_query")
        return [{"chunk_id": "c1"}], {"rerank_on": False}

    monkeypatch.setattr(ab_service.pipeline, "recall", fake_recall)
    rfn = _make_recall_fn(AblationConfig(rerank=False))
    cands, meta = await rfn(None, "q", top_k=5, semantic_query="q", terms=["q"], rewritten=False)

    assert recorded["ablation"] == AblationConfig(rerank=False)
    assert recorded["top_k"] == 5
    assert recorded["sem"] == "q"
    assert rfn.ablation == AblationConfig(rerank=False)
    assert cands == [{"chunk_id": "c1"}]


async def test_run_ab_dedup_deltas_and_rerank_counts(monkeypatch):
    calls = {"n": 0}

    async def fake_run_eval(session, queries, *, top_k, rewrite, recall_fn):
        calls["n"] += 1
        name = _AB_TO_NAME[recall_fn.ablation]
        rec, prec, mrr, ndcg = _CANNED[name]
        rerank_on_count = 0 if name == "no_rerank" else len(queries)
        return EvalReport(
            config={"top_k": top_k, "rewrite": rewrite},
            aggregate=_agg(rec, prec, mrr, ndcg, n=len(queries)),
            n_queries=len(queries),
            n_evaluable=len(queries),
            rerank_on_count=rerank_on_count,
            per_query=[],
            unresolved=[],
        )

    monkeypatch.setattr(ab_service, "run_eval", fake_run_eval)
    report = await run_ab(None, _queries(), top_k=10, rewrite="off")

    # 4 个唯一变体 → run_eval 恰调 4 次（FULL 三对共享，去重）
    assert calls["n"] == 4
    assert set(report.variants) == {"full", "no_rerank", "vector_only", "no_graph"}

    rerank = next(p for p in report.pairs if p["name"] == "rerank")
    assert rerank["delta"]["precision"][1] == {"abs": 0.3, "pct": 60.0}   # no_rerank→full
    assert rerank["delta"]["ndcg"][10]["pct"] == pytest.approx(28.57, abs=0.01)  # (0.90-0.70)/0.70

    mp = next(p for p in report.pairs if p["name"] == "multipath_rrf")
    assert mp["delta"]["recall"][10] == {"abs": 0.2, "pct": 40.0}         # vector_only→full

    g = next(p for p in report.pairs if p["name"] == "graph")
    assert g["delta"]["recall"][10]["abs"] == 0.05                        # no_graph→full
    assert g["delta"]["recall"][10]["pct"] == pytest.approx(7.69, abs=0.01)  # 0.05/0.65

    assert report.variants["full"]["rerank_on_count"] == 3
    assert report.variants["no_rerank"]["rerank_on_count"] == 0

    # JSON 可序列化
    json.dumps(report.to_dict())


async def test_run_ab_propagates_per_query(monkeypatch):
    """M25：每变体输出透传 EvalReport.per_query（含 retrieved_kinds/recall_paths），to_dict 仍可序列化。"""
    async def fake_run_eval(session, queries, *, top_k, rewrite, recall_fn):
        name = _AB_TO_NAME[recall_fn.ablation]
        rec, prec, mrr, ndcg = _CANNED[name]
        return EvalReport(
            config={"top_k": top_k, "rewrite": rewrite},
            aggregate=_agg(rec, prec, mrr, ndcg, n=len(queries)),
            n_queries=len(queries),
            n_evaluable=len(queries),
            rerank_on_count=0 if name == "no_rerank" else len(queries),
            per_query=[{"id": f"{name}_q1", "retrieved_kinds": ["code"],
                        "recall_paths": {"vector": [{"chunk_id": "c1", "kind": "code"}]}}],
            unresolved=[],
        )

    monkeypatch.setattr(ab_service, "run_eval", fake_run_eval)
    report = await run_ab(None, _queries(), top_k=10, rewrite="off")

    assert report.variants["full"]["per_query"] == [
        {"id": "full_q1", "retrieved_kinds": ["code"],
         "recall_paths": {"vector": [{"chunk_id": "c1", "kind": "code"}]}}
    ]
    assert report.variants["vector_only"]["per_query"][0]["id"] == "vector_only_q1"
    # 含 per_query 的报告仍可 JSON 序列化
    json.dumps(report.to_dict())
