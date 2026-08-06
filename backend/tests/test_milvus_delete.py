"""milvus_client.delete_vectors 单测（按 chunk_id 主键硬删除）。"""
from __future__ import annotations

from app.clients import milvus_client


class _FakeClient:
    def __init__(self) -> None:
        self.deletes: list[tuple[str, list[str]]] = []

    def delete(self, *, collection_name, ids):
        self.deletes.append((collection_name, list(ids)))


def test_delete_vectors_empty_is_noop(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("client must not be called for empty input")

    monkeypatch.setattr(milvus_client, "get_client", boom)
    assert milvus_client.delete_vectors("unified", "code", []) == 0


def test_delete_vectors_unified_uses_single_collection(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(milvus_client, "get_client", lambda: fc)
    monkeypatch.setattr(milvus_client, "ensure_collection", lambda *a, **k: None)

    n = milvus_client.delete_vectors("unified", "code", ["c1", "c2"])

    assert n == 2
    assert fc.deletes == [("coderag_vectors", ["c1", "c2"])]


def test_delete_vectors_dual_routes_by_kind(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(milvus_client, "get_client", lambda: fc)
    monkeypatch.setattr(milvus_client, "ensure_collection", lambda *a, **k: None)

    milvus_client.delete_vectors("dual", "code", ["c1"])
    milvus_client.delete_vectors("dual", "doc", ["d1"])

    assert fc.deletes[0][0] == "code_vectors"
    assert fc.deletes[1][0] == "doc_vectors"
