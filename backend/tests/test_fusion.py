"""RRF 融合单元测试（retrieval/fusion.py）。纯函数，无需外部依赖。"""
from __future__ import annotations

from app.retrieval.fusion import DEFAULT_WEIGHTS, rrf_fuse


def _rec(cid: str, score: float = 0.0, kind: str = "code") -> dict:
    return {"chunk_id": cid, "kind": kind, "content": f"c-{cid}", "score": score}


def test_single_path_preserves_order_and_score():
    ranking = {"vector": [_rec("a"), _rec("b"), _rec("c")]}
    fused = rrf_fuse(ranking, k=60)
    assert [f["chunk_id"] for f in fused] == ["a", "b", "c"]
    # 单路 vector 权重 1.0，rank 1-based：score = 1/(60+rank)
    assert fused[0]["score"] == 1.0 / (60 + 1)
    assert fused[2]["score"] == 1.0 / (60 + 3)


def test_two_paths_accumulate_and_rank_promotes_overlap():
    # a 同时被两路命中（向量 rank1 + 词法 rank2）→ RRF 分最高
    rankings = {
        "vector": [_rec("a"), _rec("b")],
        "lexical": [_rec("c"), _rec("a")],  # a 在词法排第 2
    }
    fused = rrf_fuse(rankings, k=60)
    wv, wl = DEFAULT_WEIGHTS["vector"], DEFAULT_WEIGHTS["lexical"]
    score_a = wv / (60 + 1) + wl / (60 + 2)
    score_b = wv / (60 + 2)
    score_c = wl / (60 + 1)
    # a 两路叠加最高；b（向量 rank2）> c（词法 rank1）因向量权重更高
    assert [f["chunk_id"] for f in fused] == ["a", "b", "c"]
    assert fused[0]["score"] == score_a
    assert fused[1]["score"] == score_b
    assert fused[2]["score"] == score_c


def test_dedup_by_chunk_id_and_first_seen_metadata_kept():
    # a 在两路出现，且元数据不同 → 只保留一份，元数据取首次出现路径
    rankings = {
        "vector": [{"chunk_id": "a", "kind": "code", "content": "from-vector", "score": 9.0}],
        "lexical": [{"chunk_id": "a", "kind": "doc", "content": "from-lexical", "score": 1.0}],
    }
    fused = rrf_fuse(rankings, k=60)
    assert len(fused) == 1
    # vector 在默认权重下先于 lexical 迭代（dict 保持插入序），且分更高
    assert fused[0]["content"] == "from-vector"
    assert fused[0]["kind"] == "code"
    # score 被覆盖为 RRF 分，不再是原 9.0
    assert fused[0]["score"] != 9.0


def test_missing_path_weight_ignored():
    # graph_vec 路径未传入 → 其权重 0.9 不参与，不影响结果
    fused = rrf_fuse({"vector": [_rec("a")]}, k=60)
    assert fused[0]["score"] == 1.0 / 61


def test_custom_weights_override():
    fused = rrf_fuse(
        {"lexical": [_rec("a")]}, weights={"lexical": 2.0}, k=60,
    )
    assert fused[0]["score"] == 2.0 / 61


def test_empty_rankings():
    assert rrf_fuse({}, k=60) == []
    assert rrf_fuse({"vector": []}, k=60) == []
