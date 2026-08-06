"""RRF 融合（设计 §11.4）。

Reciprocal Rank Fusion：把多路召回结果按各自排名融合，无需校准各路分数量级。

    RRF_score(d) = Σ_i  weight_i / (k + rank_i(d))

  k = 60（常数）；rank 1-based；weight 见下表。

| 路径     | 权重 | 键名     | 说明                          |
| -------- | ---- | -------- | ----------------------------- |
| 向量语义 | 1.0  | vector   | 基准                          |
| BM25/词法| 0.8  | lexical  | 精确匹配补充（含 PG 词法降级）|
| 图遍历   | 1.2  | graph    | 确定性关联，权重最高          |

> 图向量（graph_vec，原 Phase 5 路径 C）已于 2026-07-27 移除；保留图遍历（路径 D）。
"""
from __future__ import annotations

# 各路召回的 RRF 权重（与设计 §11.4 表对齐；缺失路径自动忽略）
DEFAULT_WEIGHTS: dict[str, float] = {
    "vector": 1.0,
    "lexical": 0.8,
    "graph": 1.2,
}


def rrf_fuse(
    rankings: dict[str, list[dict]],
    *,
    weights: dict[str, float] | None = None,
    k: int = 60,
) -> list[dict]:
    """多路召回结果做 RRF 融合去重。

    Args:
        rankings: {路径名: 该路按分数降序的结果列表}。每条结果至少含 ``chunk_id``。
        weights: 路径权重覆盖；默认 :data:`DEFAULT_WEIGHTS`。
        k: RRF 常数（默认 60）。

    Returns:
        融合后按 RRF 分数降序的列表；每条保留首次出现路径的完整元数据，
        ``score`` 字段被覆盖为 RRF 分数。
    """
    w = weights or DEFAULT_WEIGHTS
    scores: dict[str, float] = {}
    meta: dict[str, dict] = {}

    for path, items in rankings.items():
        weight = w.get(path, 1.0)
        for rank, rec in enumerate(items, start=1):  # 1-based rank
            cid = rec["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + weight / (k + rank)
            if cid not in meta:
                meta[cid] = dict(rec)  # 首次出现路径的元数据（content/kind/…）

    fused = [{**meta[cid], "score": s} for cid, s in scores.items()]
    fused.sort(key=lambda x: x["score"], reverse=True)
    return fused
