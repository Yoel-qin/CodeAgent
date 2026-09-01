from app.clients import embedding_client


def test_embed_texts_no_key_returns_empty(monkeypatch):
    monkeypatch.setattr("app.clients.embedding_client.settings.embedding_api_key", "")
    assert embedding_client.embed_texts(["你好"]) == []


def test_embed_texts_batches_and_parses(monkeypatch):
    calls = []

    def fake_post(url, *, json, headers, timeout):
        calls.append((json["input"], headers))
        return type("R", (), {"status_code": 200, "raise_for_status": lambda self: None,
                              "json": lambda self: {"data": [{"index": i, "embedding": [0.1, 0.2]}
                                                             for i in range(len(json["input"]))]}})()

    monkeypatch.setattr("app.clients.embedding_client.settings.embedding_api_key", "k")
    monkeypatch.setattr(embedding_client.httpx, "post", fake_post)
    vecs = embedding_client.embed_texts([f"t{i}" for i in range(35)])  # >2 批
    assert len(vecs) == 35 and vecs[0] == [0.1, 0.2]
    assert all(len(b) <= 16 for b, _ in calls)
    assert all(h["Authorization"].startswith("Bearer ") for _, h in calls)  # 认证头必传（SiliconFlow 无 key 即 401）


def test_milvus_upsert_and_search_arg_assembly(monkeypatch):
    from app.clients import milvus_client as mc
    calls = []

    class StubClient:
        def upsert(self, collection_name, data): calls.append(("upsert", collection_name, data))
        def search(self, **kw):
            calls.append(("search", kw))
            return []

    monkeypatch.setattr(mc, "get_client", lambda: StubClient())
    monkeypatch.setattr(mc, "ensure_collection", lambda: None)
    mc.upsert_sections([{"id": "s1", "embedding": [0.1] * 1024, "repo": "mini",
                         "doc_name": "a.md", "title": "T", "section": "x",
                         "module": None, "page": 1}])
    assert calls[0][1] == "v2_doc_chunks" and calls[0][2][0]["id"] == "s1"

    mc.search_sections([0.1] * 1024, top_k=5, repo="mini", module=None)
    search_kw = calls[1][1]
    assert search_kw["collection_name"] == "v2_doc_chunks"
    assert 'repo == "mini"' in search_kw["filter"], "repo 过滤必须前置进 expr"


def test_es_search_passes_repo_filter(monkeypatch):
    from app.clients import es_client as ec
    seen = {}

    class StubEs:
        def search(self, **kw):
            seen.update(kw)
            return {"hits": {"hits": [
                {"_id": "s1", "_score": 8.0,
                 "_source": {"repo": "mini", "doc_name": "a.md", "title": "T",
                             "anchor": "x", "module": None, "section_id": "s1"}}]}}

    monkeypatch.setattr(ec, "get_es", lambda: StubEs())
    res = ec.search_sections("刷盘", top_k=5, repo="mini")
    assert res[0]["section_id"] == "s1" and res[0]["score"] == 8.0
    assert "repo" in str(seen), "repo 过滤必须进 ES query"
