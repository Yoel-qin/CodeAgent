"""doc_search core 五函数的单元测试（monkeypatch clients，零外部依赖）。"""
from app.core.doc_search import hybrid_search, semantic_search


def test_semantic_no_key_empty(monkeypatch):
    monkeypatch.setattr("app.core.doc_search.embed_texts", lambda t, **k: [])
    res = semantic_search("mini", "刷盘机制")
    assert res == {"results": [], "recall": 0}


def test_hybrid_rrf_merge(monkeypatch):
    monkeypatch.setattr("app.core.doc_search.embed_texts", lambda t, **k: [[0.1] * 1024])
    monkeypatch.setattr(
        "app.core.doc_search.vector_search_sections",
        lambda q, *, top_k, repo, module: [
            {"section_id": "s1", "doc_name": "a.md", "title": "T",
             "anchor": "x", "module": None, "score": 0.9}
        ])
    monkeypatch.setattr(
        "app.core.doc_search.es_search_sections",
        lambda q, *, top_k, repo: [
            {"section_id": "s2", "doc_name": "a.md", "title": "T2",
             "anchor": "y", "module": None, "score": 8.0},
            {"section_id": "s1", "doc_name": "a.md", "title": "T",
             "anchor": "x", "module": None, "score": 7.0}
        ])
    res = hybrid_search("mini", "q")
    ids = [r["section_id"] for r in res["results"]]
    assert ids[0] == "s1", "两路都命中的 s1 RRF 必然第一"
