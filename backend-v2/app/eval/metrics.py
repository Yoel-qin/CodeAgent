"""指标聚合（M8，纯函数）。per_query 行形状见 harness.build_row（Task 4 冻结）。"""
from __future__ import annotations

import math
from statistics import fmean


def percentile(values: list[float], p: float) -> float | None:
    """最近邻分位（升序取 ``ceil(p*n)-1`` 下标）；空列表 → None。"""
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, math.ceil(p * len(ordered)) - 1)
    return ordered[idx]


def aggregate(rows: list[dict]) -> dict:
    """per_query 行列表 → 冻结键聚合。

    - 命中率分母只数「锚点已解析」的行（``has_code_anchor``/``has_doc_anchor``）——
      锚点 unresolved 的 case 不拖低命中率（分母剔除，unresolved 明细在行内可见）。
    - citation_precision 分母只数 ``total>0`` 的行（零引用行不计精度的宏观平均）。
    - 全部指标在无样本时为 None（诚实缺席，不是 0）。
    """
    code_rows = [r for r in rows if r.get("has_code_anchor")]
    doc_rows = [r for r in rows if r.get("has_doc_anchor")]
    prec_rows = [r for r in rows if r.get("total", 0) > 0 and r.get("precision") is not None]
    lat = [float(r["latency_ms"]) for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    rounds = [float(r["rounds"]) for r in rows if isinstance(r.get("rounds"), (int, float))]
    toks = [float(r["tokens"]) for r in rows if isinstance(r.get("tokens"), (int, float))]
    return {
        "n_cases": len(rows),
        "code_hit_rate": (sum(1 for r in code_rows if r["hit_code"]) / len(code_rows))
                        if code_rows else None,
        "doc_hit_rate": (sum(1 for r in doc_rows if r["hit_doc"]) / len(doc_rows))
                        if doc_rows else None,
        "citation_precision": fmean(r["precision"] for r in prec_rows) if prec_rows else None,
        "rounds_mean": fmean(rounds) if rounds else None,
        "rounds_p95": percentile(rounds, 0.95),
        "latency_p50_ms": percentile(lat, 0.5),
        "latency_p95_ms": percentile(lat, 0.95),
        "tokens_mean": fmean(toks) if toks else None,
    }
