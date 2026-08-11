"""全局搜索（⌘K）单测（无 infra）：search_service.search 纯函数 + GET /v1/search 端点。

monkeypatch lexical_recall（PG 关键词召回）返 canned code+doc 行，验证 label/snippet/score 组装、
kind 过滤；TestClient（dependency_overrides[get_db]）验证端点 200 + 缺 q → 422。
"""
from __future__ import annotations

import app.services.search_service as svc


def _canned() -> list[dict]:
    return [
        {"chunk_id": "code_Account_deposit_abc12345", "kind": "code",
         "content": "public void deposit(Money amount) { balance.add(amount); }",
         "class_name": "Account", "method_name": "deposit", "heading_path": None, "score": 3.0},
        {"chunk_id": "doc_design_tx", "kind": "doc",
         "content": "存款交易 deposit 流程：先校验金额再入账。",
         "class_name": None, "method_name": None, "heading_path": ["事务", "存款"], "score": 2.0},
    ]


# ---- _label / _snippet 纯函数 ----


def test_label_code_and_doc():
    assert svc._label({"kind": "code", "class_name": "A", "method_name": "m"}) == "A.m"
    assert svc._label({"kind": "code", "class_name": "A", "method_name": None, "chunk_id": "c"}) == "A"
    assert svc._label({"kind": "doc", "heading_path": ["事务", "存款"]}) == "事务 › 存款"


def test_snippet_around_term_head_and_empty():
    assert "deposit" in svc._snippet("check deposit funds", ["deposit"])
    assert svc._snippet(None, ["x"]) == ""
    assert svc._snippet("short content", ["nomatch"]).startswith("short")  # 无命中取头


# ---- search_service.search（monkeypatch lexical_recall）----


async def test_search_assembles_label_and_snippet(monkeypatch):
    async def fake_recall(session, terms, *, top_k=20):
        assert terms  # 经 extract_query_terms 切词后非空
        return _canned()

    monkeypatch.setattr(svc, "lexical_recall", fake_recall)
    data = await svc.search(None, "deposit 存款", top_k=12)
    assert data["q"] == "deposit 存款" and data["total"] == 2
    code, doc = data["items"]
    assert code["kind"] == "code" and code["label"] == "Account.deposit"
    assert "deposit" in code["snippet"].lower()                 # snippet 含命中词
    assert doc["kind"] == "doc" and doc["label"] == "事务 › 存款"
    assert all(isinstance(it["score"], float) for it in data["items"])


async def test_search_kind_filter(monkeypatch):
    async def fake_recall(session, terms, *, top_k=20):
        return _canned()

    monkeypatch.setattr(svc, "lexical_recall", fake_recall)
    data = await svc.search(None, "deposit", kind="code", top_k=12)
    assert data["total"] == 1
    assert data["items"][0]["kind"] == "code" and data["items"][0]["chunk_id"].startswith("code_")


# ---- GET /v1/search 端点（TestClient + dependency_overrides）----


async def test_search_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.deps import get_db
    from app.main import app

    async def fake_recall(session, terms, *, top_k=20):
        return _canned()

    monkeypatch.setattr(svc, "lexical_recall", fake_recall)

    async def _override():
        return None  # session 被 mocked 的 lexical_recall 忽略

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)
        resp = client.get("/v1/search", params={"q": "deposit"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["q"] == "deposit" and body["total"] == 2
        assert {it["kind"] for it in body["items"]} == {"code", "doc"}
        assert "snippet" in body["items"][0] and "label" in body["items"][0]
        # kind 过滤
        only_code = client.get("/v1/search", params={"q": "deposit", "kind": "code"})
        assert only_code.status_code == 200 and only_code.json()["total"] == 1
        # 缺 q → 422（Query min_length=1）
        assert client.get("/v1/search").status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
