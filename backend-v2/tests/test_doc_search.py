"""doc_search core 五函数的单元测试 + PG repo 隔离测试。"""
from app.core.doc_search import get_doc_toc, hybrid_search, read_doc_section, semantic_search
from app.db.models.doc import DocSection, Document


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


def test_read_doc_section_repo_isolation(session, monkeypatch):
    """C-1: read_doc_section 不能跨 repo 读取段落。"""
    # seed repo-a
    doc_a = Document(repo="repo-a", doc_name="a.md", module=None,
                      source_path="a.md", doc_type="markdown",
                      status="COMPLETED", file_hash="h1")
    session.add(doc_a)
    session.flush()
    sec_a = DocSection(document_id=doc_a.id, repo="repo-a", anchor="#s1",
                        title="Sec1", level=1, kind="text",
                        content="hello", token_count=1, order_index=0)
    session.add(sec_a)
    session.flush()
    # seed repo-b with same doc_id? no — different document row, different id
    doc_b = Document(repo="repo-b", doc_name="b.md", module=None,
                      source_path="b.md", doc_type="markdown",
                      status="COMPLETED", file_hash="h2")
    session.add(doc_b)
    session.flush()
    sec_b = DocSection(document_id=doc_b.id, repo="repo-b", anchor="#s1",
                        title="Sec1-B", level=1, kind="text",
                        content="world", token_count=1, order_index=0)
    session.add(sec_b)
    session.flush()

    monkeypatch.setattr("app.core.doc_search._pg_session", lambda: session)

    # same anchor, same doc_id won't happen cross-repo, but test with repo-a's doc
    res_own = read_doc_section("repo-a", doc_a.id, "#s1")
    assert res_own.get("title") == "Sec1"
    # query repo-b for repo-a's doc_id+anchor → not found (repo mismatch)
    res_cross = read_doc_section("repo-b", doc_a.id, "#s1")
    assert res_cross == {"error": "section not found"}


def test_get_doc_toc_repo_isolation(session, monkeypatch):
    """C-2: get_doc_toc 无 doc_id 时只返回本 repo 目录。"""
    for repo_name, doc_name in [("repo-x", "x.md"), ("repo-y", "y.md")]:
        d = Document(repo=repo_name, doc_name=doc_name, module=None,
                     source_path=doc_name, doc_type="markdown",
                     status="COMPLETED", file_hash=f"h_{repo_name}")
        session.add(d)
        session.flush()
        session.add(DocSection(document_id=d.id, repo=repo_name, anchor="#a",
                                title="A", level=1, kind="text",
                                content="c", token_count=1, order_index=0))
        session.flush()

    monkeypatch.setattr("app.core.doc_search._pg_session", lambda: session)

    toc_x = get_doc_toc("repo-x")
    assert len(toc_x["toc"]) == 1
    assert toc_x["toc"][0]["doc_name"] == "x.md"

    toc_y = get_doc_toc("repo-y")
    assert len(toc_y["toc"]) == 1
    assert toc_y["toc"][0]["doc_name"] == "y.md"
