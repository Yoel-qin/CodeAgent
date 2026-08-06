"""sync_soft_delete 单测：§6.4 软删除级联（标记位 + Milvus/ES 硬删）。

monkeypatch 文件/chunk 查询与 Milvus/ES 客户端，用 _FakeSession 记录 UPDATE 语句，
断言：软删（is_deleted 置真）而非硬删（无 DELETE FROM），且 Milvus/ES 收到正确参数。
"""
from __future__ import annotations

from app.clients import es_client
from app.pipeline import indexing, sync_soft_delete


class _Result:
    def __init__(self, rowcount: int = 0):
        self.rowcount = rowcount

    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.executed: list = []

    def execute(self, stmt):
        self.executed.append(stmt)
        return _Result(rowcount=1)

    def get(self, model, pk):
        return None


class _FakeFile:
    def __init__(self, file_id: int) -> None:
        self.file_id = file_id


def test_soft_delete_marks_flags_and_deletes_stores(monkeypatch):
    monkeypatch.setattr(sync_soft_delete, "_get_file_row", lambda s, kind, fp: _FakeFile(7))
    monkeypatch.setattr(sync_soft_delete, "_chunk_ids", lambda s, kind, fid: ["c1", "c2"])
    milvus_calls: list = []
    monkeypatch.setattr(indexing, "delete_chunks_from_milvus",
                        lambda strat, kind, ids: milvus_calls.append((kind, list(ids))) or True)
    es_calls: list = []
    monkeypatch.setattr(es_client, "delete_by_file", lambda fp: es_calls.append(fp))

    fs = _FakeSession()
    res = sync_soft_delete.soft_delete_file(
        fs, file_path="Foo.java", kind="code", delete_commit="D1")

    assert res["chunks"] == 2
    assert res["chunk_ids"] == ["c1", "c2"]
    assert milvus_calls == [("code", ["c1", "c2"])]
    assert es_calls == ["Foo.java"]
    # 软删：UPDATE ... is_deleted，而非 DELETE FROM
    sqls = " ".join(str(s) for s in fs.executed).upper()
    assert "IS_DELETED" in sqls
    assert "DELETE FROM" not in sqls


def test_soft_delete_unknown_file_returns_zeros(monkeypatch):
    monkeypatch.setattr(sync_soft_delete, "_get_file_row", lambda s, kind, fp: None)
    res = sync_soft_delete.soft_delete_file(
        _FakeSession(), file_path="X.java", kind="code", delete_commit="D")
    assert res["chunks"] == 0
    assert res["chunk_ids"] == []


def test_soft_delete_file_with_no_active_chunks(monkeypatch):
    monkeypatch.setattr(sync_soft_delete, "_get_file_row", lambda s, kind, fp: _FakeFile(7))
    monkeypatch.setattr(sync_soft_delete, "_chunk_ids", lambda s, kind, fid: [])
    res = sync_soft_delete.soft_delete_file(
        _FakeSession(), file_path="X.java", kind="code", delete_commit="D")
    assert res["chunks"] == 0
