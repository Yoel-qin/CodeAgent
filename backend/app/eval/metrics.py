"""检索质量评测指标（纯函数，二值相关）。

IR 标准定义，零依赖、可单测（仿 ``tests/test_fusion.py`` 的纯函数风格）。

- ``retrieved``：系统返回的**有序** chunk_id 列表（rank 从 1 起）。
- ``relevant``：人工标注的相关 chunk_id 集合。
- 二值相关：命中=1，否则=0。

公式：
- Recall@K    = |relevant ∩ retrieved[:K]| / |relevant|
- Precision@K = |relevant ∩ retrieved[:K]| / K
- MRR         = 1 / rank（首个相关位）；无命中 → 0
- NDCG@K      = DCG@K / IDCG@K，DCG = Σ rel_i / log2(i+1)（i 为 1-based 位次）
"""
from __future__ import annotations

import math
from collections.abc import Sequence

DEFAULT_KS: tuple[int, ...] = (1, 3, 5, 10)


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Recall@K。``relevant`` 为空或 k<=0 → 0.0（调用方应跳过空 relevant 不计入分母）。"""
    if not relevant or k <= 0:
        return 0.0
    hits = sum(1 for cid in retrieved[:k] if cid in relevant)
    return hits / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Precision@K。"""
    if k <= 0:
        return 0.0
    hits = sum(1 for cid in retrieved[:k] if cid in relevant)
    return hits / k


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float:
    """单查询 MRR：1/rank（首个相关位）；无命中 → 0.0。"""
    for i, cid in enumerate(retrieved, start=1):
        if cid in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """NDCG@K（二值相关）。无相关或 k<=0 → 0.0。"""
    if not relevant or k <= 0:
        return 0.0
    top = retrieved[:k]
    # DCG：位次 i（1-based）贡献 rel_i / log2(i+1)
    dcg = sum((1.0 if cid in relevant else 0.0) / math.log2(i + 1) for i, cid in enumerate(top, start=1))
    # IDCG：理想排序把所有相关项放最前（最多 min(|relevant|, k) 个命中）
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def first_hit_rank(retrieved: Sequence[str], relevant: set[str]) -> int | None:
    """首个相关项的 1-based 位次；无命中 → None。"""
    for i, cid in enumerate(retrieved, start=1):
        if cid in relevant:
            return i
    return None


def evaluate_query(
    retrieved: Sequence[str], relevant: set[str] | Sequence[str],
    ks: Sequence[int] = DEFAULT_KS,
) -> dict:
    """单查询全指标：recall/precision/ndcg（按 K）+ mrr + first_hit_rank。"""
    rel = set(relevant)
    return {
        "recall": {k: round(recall_at_k(retrieved, rel, k), 4) for k in ks},
        "precision": {k: round(precision_at_k(retrieved, rel, k), 4) for k in ks},
        "mrr": round(reciprocal_rank(retrieved, rel), 4),
        "ndcg": {k: round(ndcg_at_k(retrieved, rel, k), 4) for k in ks},
        "first_hit_rank": first_hit_rank(retrieved, rel),
    }


def aggregate(per_query: list[dict], ks: Sequence[int] = DEFAULT_KS) -> dict:
    """宏平均 over ``per_query``（list of ``evaluate_query`` 结果）。空 → 各指标 None。"""
    n = len(per_query)
    if n == 0:
        return {"n": 0, "recall": {k: None for k in ks},
                "precision": {k: None for k in ks}, "mrr": None, "ndcg": {k: None for k in ks}}

    def _mean_key(metric: str) -> dict:
        out: dict = {}
        for k in ks:
            vals = [pq[metric][k] for pq in per_query if pq.get(metric) and pq[metric].get(k) is not None]
            out[k] = round(sum(vals) / len(vals), 4) if vals else None
        return out

    def _mean(metric: str) -> float | None:
        vals = [pq[metric] for pq in per_query if pq.get(metric) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    return {
        "n": n,
        "recall": _mean_key("recall"),
        "precision": _mean_key("precision"),
        "mrr": _mean("mrr"),
        "ndcg": _mean_key("ndcg"),
    }
