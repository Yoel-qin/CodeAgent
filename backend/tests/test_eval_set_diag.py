"""M38 诊断 eval 集：eval_set_diag.yaml 结构自洽（M40 diagnosis runner 才用）。"""
from __future__ import annotations

from pathlib import Path

import yaml

_EVAL = Path(__file__).resolve().parents[1] / "eval" / "eval_set_diag.yaml"


def _load():
    with _EVAL.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_eval_set_diag_parses():
    data = _load()
    assert data["version"] == 1
    assert "queries" in data
    assert len(data["queries"]) == 10


def test_each_query_has_required_fields():
    for q in _load()["queries"]:
        assert "id" in q and "text" in q and "intent" in q
        assert "expected" in q
        assert isinstance(q["expected"].get("root_cause_hints"), list)
        assert isinstance(q["expected"].get("relevant_code"), list)
        assert isinstance(q["expected"].get("config_suggestions"), list)
        assert "rubric" in q


def test_rubric_weights_sum_to_one():
    """每 query rubric 权重和 ≈ 1.0（容浮点误差）。"""
    for q in _load()["queries"]:
        total = sum(q["rubric"].values())
        assert abs(total - 1.0) < 1e-9, f"{q['id']} rubric 权重和={total}，应为 1.0"


def test_intents_cover_diagnose_tune_trace():
    intents = {q["intent"] for q in _load()["queries"]}
    assert "diagnose" in intents
    assert "tune" in intents
    assert "trace" in intents
