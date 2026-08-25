"""``recall`` 消融开关单测（retrieval/pipeline.py 的 ``ablation`` 钩子）。

无 infra：monkeypatch 三路召回 + 精排为记录型假函数（仿 ``tests/test_indexing.py``），
断言 ``AblationConfig`` 各 ``False`` 字段确实短路对应环节，且 ``ablation=None`` 与
``full()`` 行为一致；生产默认路径不变。
"""
from __future__ import annotations

from app.clients import reranker_client
from app.retrieval import pipeline as pipeline_mod
from app.retrieval.ablation import AblationConfig

# 绕过 Stage-0 LLM 改写的固定 kwargs（与 eval_service rewrite="off" 一致）
_KW = {"semantic_query": "query", "terms": ["query"], "rewritten": False}


class _FakeSession:
    async def execute(self, *a, **k):  # 假召回均忽略 session；留 no-op 以防遗漏
        return []


def _patch(monkeypatch):
    """把三路召回 + 精排换成记录型 async 假函数；reranker 强制 enabled。返回调用计数 dict。"""
    calls = {"vector": 0, "bm25": 0, "lexical": 0, "graph": 0, "rerank": 0}

    async def v_recall(session, sem, *, top_k, **kwargs):
        calls["vector"] += 1
        return [{"chunk_id": "v1", "kind": "code", "content": "vc", "score": 0.9}]

    async def b_recall(sem, *, top_k, **kwargs):
        calls["bm25"] += 1
        return [{"chunk_id": "b1", "kind": "code", "content": "bc", "score": 0.8}]

    async def l_recall(session, terms, *, top_k, **kwargs):
        calls["lexical"] += 1
        return [{"chunk_id": "l1", "kind": "code", "content": "lc", "score": 0.7}]

    async def g_recall(session, seed_ids, *, depth, max_nodes, **kw):
        calls["graph"] += 1
        return [{"chunk_id": "g1", "kind": "code", "content": "gc", "score": 0.6}]

    async def r_stage(query, candidates, *, model, top_n):
        calls["rerank"] += 1
        return list(candidates)

    monkeypatch.setattr(pipeline_mod, "vector_recall", v_recall)
    monkeypatch.setattr(pipeline_mod, "bm25_recall", b_recall)
    monkeypatch.setattr(pipeline_mod, "lexical_recall", l_recall)
    monkeypatch.setattr(pipeline_mod, "graph_recall", g_recall)
    monkeypatch.setattr(pipeline_mod, "rerank_stage", r_stage)
    monkeypatch.setattr(reranker_client, "enabled", lambda: True)
    return calls


async def _recall(monkeypatch, ablation):
    calls = _patch(monkeypatch)
    _, meta = await pipeline_mod.pipeline.recall(
        _FakeSession(), "query", top_k=5, ablation=ablation, **_KW
    )
    return calls, meta


async def test_ablation_none_is_full_pipeline(monkeypatch):
    calls, meta = await _recall(monkeypatch, None)
    assert calls == {"vector": 1, "bm25": 1, "lexical": 0, "graph": 1, "rerank": 1}
    assert meta["recall"] == {"vector": 1, "lexical": 1, "graph": 1}  # lexical=BM25 结果
    assert meta["rerank_on"] is True


async def test_ablation_full_equals_none(monkeypatch):
    """显式 full() 与 None 行为一致（生产零行为变更的核心保证）。"""
    c_none, m_none = await _recall(monkeypatch, None)
    c_full, m_full = await _recall(monkeypatch, AblationConfig())
    assert c_none == c_full
    assert m_none["recall"] == m_full["recall"]
    assert m_none["rerank_on"] == m_full["rerank_on"]


async def test_ablation_no_graph_skips_graph(monkeypatch):
    calls, meta = await _recall(monkeypatch, AblationConfig(graph=False))
    assert calls["graph"] == 0
    assert meta["recall"]["graph"] == 0
    assert calls["vector"] == 1 and calls["bm25"] == 1   # 其余路照常
    assert meta["rerank_on"] is True


async def test_ablation_vector_only(monkeypatch):
    calls, meta = await _recall(monkeypatch, AblationConfig(lexical=False, graph=False))
    assert calls["bm25"] == 0 and calls["lexical"] == 0 and calls["graph"] == 0
    assert calls["vector"] == 1
    assert meta["recall"] == {"vector": 1, "lexical": 0, "graph": 0}
    assert meta["rerank_on"] is True   # rerank 仍开（对向量候选重排）


async def test_ablation_no_rerank_skips_rerank(monkeypatch):
    calls, meta = await _recall(monkeypatch, AblationConfig(rerank=False))
    assert calls["rerank"] == 0
    assert meta["rerank_on"] is False
    assert calls["vector"] == 1 and calls["bm25"] == 1 and calls["graph"] == 1  # 召回照常


async def test_ablation_no_vector(monkeypatch):
    calls, meta = await _recall(monkeypatch, AblationConfig(vector=False))
    assert calls["vector"] == 0
    assert meta["recall"]["vector"] == 0
    assert meta["vector_on"] is False


async def test_recall_emits_recall_paths_meta(monkeypatch):
    """M25：meta 带 recall_paths（三路候选 chunk_id+kind 投影），既有 count 键不破。"""
    calls, meta = await _recall(monkeypatch, None)
    assert set(meta["recall_paths"]) == {"vector", "lexical", "graph"}
    for path in ("vector", "lexical", "graph"):
        items = meta["recall_paths"][path]
        assert isinstance(items, list)
        assert all(set(c) == {"chunk_id", "kind"} for c in items)
    # vector 路含 _patch 返回的 v1（kind=code）
    assert meta["recall_paths"]["vector"] == [{"chunk_id": "v1", "kind": "code"}]
    # 既有 count 断言仍成立（加性改动）
    assert meta["recall"] == {"vector": 1, "lexical": 1, "graph": 1}


# ---------- M32 ②：多跳接线 ----------

def test_parse_relation_types_rules():
    from app.retrieval.pipeline import _parse_relation_types
    assert _parse_relation_types("") is None
    assert _parse_relation_types("   ") is None
    assert _parse_relation_types("calls,implements") == ["calls", "implements"]
    assert _parse_relation_types("calls,bogus") == ["calls"]          # 未知忽略
    assert _parse_relation_types("bogus,none") is None                # 全无效 → None


async def test_multihop_off_calls_graph_recall_with_legacy_kwargs(monkeypatch):
    """off（默认）：调用参数与旧版完全一致（depth=1/max_nodes=12/无 relation_types）。"""
    import app.retrieval.pipeline as pm

    captured = {}

    async def g_recall(session, seed_ids, *, depth, max_nodes, **kw):
        captured.update(depth=depth, max_nodes=max_nodes, kw=kw)
        return []

    _patch(monkeypatch)  # patch other召回函数（不计数）
    monkeypatch.setattr(pm, "graph_recall", g_recall)
    monkeypatch.setattr(pm.settings, "graph_multihop_enabled", False)
    await pm.pipeline.recall(_FakeSession(), "query", top_k=5, ablation=None, **_KW)
    assert captured == {"depth": 1, "max_nodes": 12, "kw": {}}


async def test_multihop_on_uses_settings(monkeypatch):
    import app.retrieval.pipeline as pm

    captured = {}

    async def g_recall(session, seed_ids, *, depth, max_nodes, **kw):
        captured.update(depth=depth, max_nodes=max_nodes, **kw)
        return []

    _patch(monkeypatch)  # patch other召回函数（不计数）
    monkeypatch.setattr(pm, "graph_recall", g_recall)
    monkeypatch.setattr(pm.settings, "graph_multihop_enabled", True)
    monkeypatch.setattr(pm.settings, "graph_traverse_depth", 3)
    monkeypatch.setattr(pm.settings, "graph_max_nodes", 40)
    monkeypatch.setattr(pm.settings, "graph_relation_types", "calls,extends")
    await pm.pipeline.recall(_FakeSession(), "query", top_k=5, ablation=None, **_KW)
    assert captured == {"depth": 3, "max_nodes": 40, "relation_types": ["calls", "extends"]}
