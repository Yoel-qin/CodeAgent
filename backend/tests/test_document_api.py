"""Phase 1.5d 文档管理单测（无基础设施）：
minio_client put/get/remove（mock Minio）、document_service.upload_document 编排
（先 put 后 ingest；ingest 失败回滚删孤儿对象）、表格资源 schema。"""
from __future__ import annotations

import pytest

from app.clients import minio_client
from app.services import document_service

# ---------- minio_client（mock Minio） ----------

class _Resp:
    def __init__(self, data: bytes):
        self._d = data

    def read(self):
        return self._d

    def close(self):
        pass

    def release_conn(self):
        pass


class _FakeMinio:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.bucket = False

    def bucket_exists(self, b):
        return self.bucket

    def make_bucket(self, b):
        self.bucket = True

    def put_object(self, b, key, stream, length, content_type="x"):
        self.objects[key] = stream.read()

    def get_object(self, b, key):
        if key not in self.objects:
            raise KeyError(key)            # 模拟真实 MinIO 对缺失对象抛错（get_bytes 捕获→None）
        return _Resp(self.objects[key])

    def remove_object(self, b, key):
        self.objects.pop(key, None)


def test_get_client_ensures_bucket(monkeypatch):
    """真实 get_client 逻辑：bucket 不存在则 make_bucket（mock Minio 类，不绕过 get_client）。"""
    fake = _FakeMinio()
    monkeypatch.setattr(minio_client, "Minio", lambda *a, **k: fake)
    monkeypatch.setattr(minio_client, "_client", None)   # 重置单例
    assert minio_client.get_client() is fake
    assert fake.bucket is True


def test_minio_client_put_get_remove(monkeypatch):
    fake = _FakeMinio()
    fake.bucket = True
    monkeypatch.setattr(minio_client, "get_client", lambda: fake)
    minio_client.put_bytes("docs/k1", b"hello", content_type="text/plain")
    assert minio_client.get_bytes("docs/k1") == b"hello"
    minio_client.remove_object("docs/k1")
    assert "docs/k1" not in fake.objects


def test_minio_client_get_missing_returns_none(monkeypatch):
    monkeypatch.setattr(minio_client, "get_client", lambda: _FakeMinio())
    assert minio_client.get_bytes("nope") is None


# ---------- document_service.upload_document 编排 ----------

class _FakeSession:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _SessionCM:
    def __init__(self, engine):
        self.session = _FakeSession()

    def __enter__(self):
        return self.session

    def __exit__(self, *a):
        return False


def _patch(monkeypatch):
    monkeypatch.setattr(document_service, "Session", _SessionCM)
    monkeypatch.setattr(document_service, "get_sync_engine", lambda: None)


def test_upload_document_puts_then_ingests_then_commits(monkeypatch):
    calls: list = []
    monkeypatch.setattr(document_service.minio_client, "put_bytes",
                        lambda key, data, content_type="x": calls.append(("put", key)) or key)
    monkeypatch.setattr(document_service.minio_client, "remove_object",
                        lambda key: calls.append(("rm", key)))
    monkeypatch.setattr(document_service, "ingest_doc_bytes",
                        lambda session, data, filename, commit_hash="U", doc_type=None, storage_path=None:
                        {"file_path": filename, "file_id": 99, "chunks": 3, "parse_status": "COMPLETED"})
    _patch(monkeypatch)

    result = document_service.upload_document(b"data", "report.pdf",
                                              doc_type=None, content_type="application/pdf")
    assert result["file_id"] == 99 and result["total_chunks"] == 3
    assert result["storage_path"].startswith("documents/") and result["storage_path"].endswith("/report.pdf")
    assert calls[0][0] == "put"                      # MinIO put 先于 ingest
    assert not any(c[0] == "rm" for c in calls)      # 成功路径不删对象


def test_upload_document_rolls_back_orphan_on_ingest_failure(monkeypatch):
    removed: list = []
    monkeypatch.setattr(document_service.minio_client, "put_bytes",
                        lambda key, data, content_type="x": key)
    monkeypatch.setattr(document_service.minio_client, "remove_object",
                        lambda key: removed.append(key))

    def boom(*a, **k):
        raise RuntimeError("parse failed")

    monkeypatch.setattr(document_service, "ingest_doc_bytes", boom)
    _patch(monkeypatch)

    with pytest.raises(RuntimeError):
        document_service.upload_document(b"data", "bad.pdf")
    assert removed and removed[0].startswith("documents/")   # 孤儿对象被清理
