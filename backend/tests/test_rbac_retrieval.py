"""RBAC 检索过滤单测（零 infra）：pipeline 穿透 + 图跳过 + 兜底 + lexical 门控 + milvus/es expr。"""
from __future__ import annotations

# ---- pipeline：穿透 + 图跳过 + 兜底（monkeypatch 四路召回）----


async def test_pipeline_threads_allowed_kinds_and_skips_graph(monkeypatch):
    import app.retrieval.pipeline as pl

    calls: dict = {}

    async def fake_vector(session, q, *, top_k=20, allowed_kinds=None):
        calls["vector"] = allowed_kinds
        return [{"chunk_id": "doc_a", "kind": "doc", "content": "x", "score": 1.0}]

    async def fake_bm25(q, *, top_k=20, allowed_kinds=None):
        calls["bm25"] = allowed_kinds
        return []  # 空 → 触发 lexical 降级

    async def fake_lexical(session, terms, *, top_k=20, allowed_kinds=None):
        calls["lexical"] = allowed_kinds
        return [{"chunk_id": "code_b", "kind": "code", "content": "y", "score": 1.0},
                {"chunk_id": "doc_c", "kind": "doc", "content": "z", "score": 0.9}]

    async def fake_graph(session, seeds, *, depth=1, max_nodes=12):
        calls["graph"] = "CALLED"
        return []

    monkeypatch.setattr(pl, "vector_recall", fake_vector)
    monkeypatch.setattr(pl, "bm25_recall", fake_bm25)
    monkeypatch.setattr(pl, "lexical_recall", fake_lexical)
    monkeypatch.setattr(pl, "graph_recall", fake_graph)

    ranked, meta = await pl.pipeline.recall(
        None, "查询", semantic_query="查询", terms=["查询"], rewritten=False,
        allowed_kinds={"doc"},
    )
    assert calls["vector"] == {"doc"} and calls["bm25"] == {"doc"}
    assert calls["lexical"] == {"doc"}
    assert "graph" not in calls                       # 无 code 权限 → 整路跳过
    assert all(r["kind"] == "doc" for r in ranked)    # code_b 被兜底滤掉


async def test_pipeline_none_means_unfiltered(monkeypatch):
    import app.retrieval.pipeline as pl

    async def fake_vector(session, q, *, top_k=20, allowed_kinds=None):
        assert allowed_kinds is None
        return [{"chunk_id": "code_b", "kind": "code", "content": "y", "score": 1.0}]

    async def fake_bm25(q, *, top_k=20, allowed_kinds=None):
        return []

    async def fake_lexical(session, terms, *, top_k=20, allowed_kinds=None):
        return []

    async def fake_graph(session, seeds, *, depth=1, max_nodes=12):
        return [{"chunk_id": "code_g", "kind": "code", "content": "g", "score": 0.5}]

    monkeypatch.setattr(pl, "vector_recall", fake_vector)
    monkeypatch.setattr(pl, "bm25_recall", fake_bm25)
    monkeypatch.setattr(pl, "lexical_recall", fake_lexical)
    monkeypatch.setattr(pl, "graph_recall", fake_graph)

    ranked, _ = await pl.pipeline.recall(
        None, "q", semantic_query="q", terms=["q"], rewritten=False)
    assert {r["chunk_id"] for r in ranked} == {"code_b", "code_g"}   # 不过滤 = 现状


# ---- lexical：SQL 门控（FakeSession 记录跑了哪路 SQL）----


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self._rows


class _Sess:
    def __init__(self):
        self.ran: list[str] = []

    async def execute(self, sql, params=None):
        s = str(sql)
        which = "code" if "code_chunks" in s else "doc"
        self.ran.append(which)
        return _Res([])


async def test_lexical_skips_code_sql_when_denied():
    from app.retrieval.lexical_search import lexical_recall

    sess = _Sess()
    await lexical_recall(sess, ["deposit"], allowed_kinds={"doc"})
    assert sess.ran == ["doc"]                    # code SQL 未执行

    sess2 = _Sess()
    await lexical_recall(sess2, ["deposit"], allowed_kinds={"code"})
    assert sess2.ran == ["code"]

    sess3 = _Sess()
    await lexical_recall(sess3, ["deposit"])
    assert sess3.ran == ["code", "doc"]           # 默认两路（现状）


# ---- milvus / es：expr 与 filter 参数 ----


def test_milvus_search_builds_kind_in_expr(monkeypatch):
    import app.clients.milvus_client as mc

    captured: dict = {}

    class _FakeClient:
        def has_collection(self, name):
            return True

        def search(self, **kw):
            captured.update(kw)
            return [[{"id": "doc_a", "entity": {"kind": "doc"}, "distance": 0.9}]]

    monkeypatch.setattr(mc, "get_client", lambda: _FakeClient())
    out = mc.search("unified", None, [0.1, 0.2], 5, allowed_kinds=["doc", "table"])
    assert captured["filter"] == 'kind in ["doc", "table"]'
    assert out[0]["kind"] == "doc"

    captured.clear()
    mc.search("unified", None, [0.1, 0.2], 5)          # 不传 = 不过滤（现状）
    assert captured["filter"] == ""


def test_es_search_adds_kind_filter(monkeypatch):
    import app.clients.es_client as ec

    captured: dict = {}

    class _FakeES:
        def search(self, index, **kw):
            captured.update(kw)
            return {"hits": {"hits": []}}

    monkeypatch.setattr(ec, "get_es", lambda: _FakeES())
    monkeypatch.setattr(ec, "ensure_index", lambda: None)
    ec.search(["存款"], "存款 流程", 10, kinds=["doc"])
    assert captured["query"]["bool"]["filter"] == [{"terms": {"kind": ["doc"]}}]

    captured.clear()
    ec.search(["存款"], "存款", 10)
    assert "filter" not in captured["query"]["bool"]
