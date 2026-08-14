"""baseline 快照对比单测:纯函数 + tmp_path,无网络/DB。"""
from __future__ import annotations

import pytest

from app.eval.baseline import compare_baseline, load_baseline, write_baseline

_BASE = {"root_cause": 0.8, "code_ref": 0.7, "config_advice": 0.6, "reasoning": 0.9, "overall": 0.75}


def test_compare_ok_on_improvement_and_flat():
    cur = {**_BASE, "root_cause": 0.9}  # 改善
    assert compare_baseline(cur, _BASE)["ok"] is True
    assert compare_baseline(dict(_BASE), dict(_BASE))["ok"] is True  # 持平


def test_compare_flags_regression():
    cur = {**_BASE, "code_ref": 0.55}  # 0.7-0.55=0.15 > 0.05
    r = compare_baseline(cur, _BASE)
    assert r["ok"] is False
    assert len(r["regressions"]) == 1
    reg = r["regressions"][0]
    assert reg["metric"] == "code_ref" and reg["current"] == 0.55 and reg["baseline"] == 0.7
    assert reg["delta"] == -0.15


def test_compare_threshold_boundary_not_regression():
    """降恰好等于阈值(0.05)不算退化:判定为 current < baseline - threshold(严格小于)。"""
    cur = {**_BASE, "code_ref": 0.65}  # 0.7-0.65=0.05,不满足 < 0.65-ε 的严格退化
    assert compare_baseline(cur, _BASE)["ok"] is True
    # 自定义阈值同理
    assert compare_baseline({**_BASE, "code_ref": 0.6}, _BASE, threshold=0.1)["ok"] is True


def test_compare_missing_metric_fails():
    cur = {k: v for k, v in _BASE.items() if k != "overall"}  # 缺 overall
    r = compare_baseline(cur, _BASE)
    assert r["ok"] is False and r["missing"] == ["overall"]
    # baseline 缺 + current 值为 None 同理
    r2 = compare_baseline(_BASE, {k: v for k, v in _BASE.items() if k != "reasoning"})
    assert r2["ok"] is False and r2["missing"] == ["reasoning"]


def test_write_load_roundtrip(tmp_path):
    p = tmp_path / "baseline_diag.json"
    meta = {"date": "2026-08-14T00:00:00+00:00", "n_queries": 10, "top_k": 8}
    write_baseline(_BASE, meta, p)
    snap = load_baseline(p)
    assert snap["metrics"] == _BASE and snap["meta"] == meta
    # 中文 ensure_ascii=False
    write_baseline({"root_cause": 0.5}, {"note": "堆积"}, p)
    assert "堆积" in p.read_text(encoding="utf-8")


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_baseline(tmp_path / "nope.json")
