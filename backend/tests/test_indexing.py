"""索引一致性层单测（pipeline/indexing.py）。

无外部依赖：monkeypatch 编码器/Milvus 客户端单例与模块内函数（_load_unsynced_chunks /
_embed_enabled_for / _mark_synced），用 _FakeSession 记录 commit/execute，验证批处理、
部分失败容错、补偿只标记成功批、编码器禁用时 no-op、每批提交。
"""
from __future__ import annotations

import pytest

from app.clients import embedding_client, milvus_client
from app.core.config import settings
from app.pipeline import indexing


class _Spec:
    """duck-typed chunk：模拟 ORM 行 / parser spec（都暴露 method_signature/javadoc/content）。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeSession:
    """记录 commit 次数；execute/flush/rollback 为 no-op。"""

    def __init__(self):
        self.commits = 0

    def execute(self, stmt):
        return []

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def flush(self):
        pass


@pytest.fixture
def reset_batch():
    saved = settings.embed_batch_size
    yield
    settings.embed_batch_size = saved


# ---- embed_text_for ----

def test_embed_text_for_code_combines_signature_javadoc_content():
    spec = _Spec(method_signature="void foo()", javadoc="docs", content="body")
    assert indexing.embed_text_for("code", spec) == "void foo()\ndocs\nbody"


def test_embed_text_for_code_filters_empty_segments():
    spec = _Spec(method_signature="void foo()", javadoc=None, content="body")
    assert indexing.embed_text_for("code", spec) == "void foo()\nbody"


def test_embed_text_for_doc_is_raw_content():
    spec = _Spec(content="章节正文")
    assert indexing.embed_text_for("doc", spec) == "章节正文"


# ---- index_chunks_to_milvus：批处理 ----

def test_index_chunks_to_milvus_batches_by_embed_batch_size(monkeypatch, reset_batch):
    settings.embed_batch_size = 2
    rows = [{"chunk_id": f"c{i}", "text": f"t{i}"} for i in range(5)]
    upsert_sizes: list[int] = []

    monkeypatch.setattr(embedding_client, "ingest_embed",
                        lambda kind, texts: [[0.0] * 8 for _ in texts])
    monkeypatch.setattr(milvus_client, "upsert_vectors",
                        lambda strategy, kind, recs: upsert_sizes.append(len(recs)))

    ok = indexing.index_chunks_to_milvus("unified", "code", rows)

    assert ok is True
    assert upsert_sizes == [2, 2, 1]  # 5 行按 batch=2 切成 (2,2,1)


def test_index_chunks_to_milvus_returns_false_on_partial_failure(monkeypatch, reset_batch):
    settings.embed_batch_size = 2
    rows = [{"chunk_id": f"c{i}", "text": f"t{i}"} for i in range(5)]
    calls = {"n": 0}

    def fake_embed(kind, texts):
        calls["n"] += 1
        if calls["n"] == 2:  # 第 2 批抛错
            raise RuntimeError("boom")
        return [[0.0] * 8 for _ in texts]

    upsert_sizes: list[int] = []
    monkeypatch.setattr(embedding_client, "ingest_embed", fake_embed)
    monkeypatch.setattr(milvus_client, "upsert_vectors",
                        lambda strategy, kind, recs: upsert_sizes.append(len(recs)))

    ok = indexing.index_chunks_to_milvus("unified", "code", rows)

    assert ok is False                      # 有批次失败
    assert upsert_sizes == [2, 1]           # batch2 未 upsert，batch1/batch3 正常


def test_index_chunks_to_milvus_empty_rows_is_noop(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("client should not be called for empty input")

    monkeypatch.setattr(embedding_client, "ingest_embed", boom)
    monkeypatch.setattr(milvus_client, "upsert_vectors", boom)

    assert indexing.index_chunks_to_milvus("unified", "code", []) is True


# ---- resync_pending_embeddings ----

def test_resync_flips_synced_only_for_succeeded_batches(monkeypatch, reset_batch):
    settings.embed_batch_size = 2

    def fake_load(session, kind, limit):
        if kind == "code":
            return [{"chunk_id": f"c{i}", "text": f"t{i}"} for i in range(4)]
        return []

    results = [True, False]  # code 4 行 / batch=2 → 2 批
    state = {"i": 0}

    def fake_milvus(strategy, kind, batch):
        r = results[state["i"]]
        state["i"] += 1
        return r

    monkeypatch.setattr(indexing, "_embed_enabled_for", lambda strategy, kind: True)
    monkeypatch.setattr(indexing, "_load_unsynced_chunks", fake_load)
    monkeypatch.setattr(indexing, "index_chunks_to_milvus", fake_milvus)
    marked: list[tuple] = []
    monkeypatch.setattr(indexing, "_mark_synced",
                        lambda session, kind, ids: marked.append((kind, list(ids))))

    session = _FakeSession()
    res = indexing.resync_pending_embeddings(session, strategy="unified", commit_each_batch=False)

    assert marked == [("code", ["c0", "c1"])]      # 仅成功批被标记；失败批 (c2,c3) 未标记
    assert res["code"] == {"total": 4, "synced": 2, "failed": 2, "skipped": False}
    assert res["doc"] == {"total": 0, "synced": 0, "failed": 0, "skipped": False}


def test_resync_does_not_abort_on_batch_failure(monkeypatch, reset_batch):
    settings.embed_batch_size = 2

    monkeypatch.setattr(indexing, "_embed_enabled_for", lambda strategy, kind: True)
    monkeypatch.setattr(indexing, "_load_unsynced_chunks",
                        lambda session, kind, limit: [{"chunk_id": f"x{i}", "text": "t"} for i in range(3)]
                        if kind == "code" else [])
    monkeypatch.setattr(indexing, "index_chunks_to_milvus", lambda *a, **k: False)  # 全失败
    monkeypatch.setattr(indexing, "_mark_synced",
                        lambda *a, **k: pytest.fail("failed batches must not be marked"))

    res = indexing.resync_pending_embeddings(_FakeSession(), strategy="unified", commit_each_batch=False)

    assert res["code"]["failed"] == 3
    assert res["code"]["synced"] == 0


def test_resync_noop_when_encoder_disabled(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("disabled path must not touch DB or clients")

    monkeypatch.setattr(indexing, "_embed_enabled_for", lambda strategy, kind: False)
    monkeypatch.setattr(indexing, "_load_unsynced_chunks", boom)
    monkeypatch.setattr(indexing, "index_chunks_to_milvus", boom)
    monkeypatch.setattr(indexing, "_mark_synced", boom)

    res = indexing.resync_pending_embeddings(_FakeSession(), strategy="unified")

    for kind in ("code", "doc"):
        assert res[kind]["skipped"] is True
        assert res[kind]["synced"] == 0


def test_resync_commit_each_batch(monkeypatch, reset_batch):
    settings.embed_batch_size = 2

    def fake_load(session, kind, limit):  # code/doc 各 2 行 → 各 1 批，均成功
        return [{"chunk_id": f"{kind[0]}{i}", "text": "t"} for i in range(2)]

    monkeypatch.setattr(indexing, "_embed_enabled_for", lambda strategy, kind: True)
    monkeypatch.setattr(indexing, "_load_unsynced_chunks", fake_load)
    monkeypatch.setattr(indexing, "index_chunks_to_milvus", lambda *a, **k: True)
    monkeypatch.setattr(indexing, "_mark_synced", lambda *a, **k: None)

    session_on = _FakeSession()
    indexing.resync_pending_embeddings(session_on, strategy="unified", commit_each_batch=True)
    assert session_on.commits == 2  # 1 批/种 × 2 种

    session_off = _FakeSession()
    indexing.resync_pending_embeddings(session_off, strategy="unified", commit_each_batch=False)
    assert session_off.commits == 0


# ---- M25：code_bge 就绪判断 / 对称删除 / reindex ----

def test_embed_enabled_for_code_bge(monkeypatch):
    """code_bge 仅在 dual + 总开关 + BGE API 就绪时启用。"""
    monkeypatch.setattr(embedding_client, "enabled", lambda: True)
    monkeypatch.setattr(embedding_client, "code_enabled", lambda: True)
    saved = settings.dual_code_bgem3_enabled
    try:
        settings.dual_code_bgem3_enabled = True
        assert indexing._embed_enabled_for("dual", "code_bge") is True

        settings.dual_code_bgem3_enabled = False
        assert indexing._embed_enabled_for("dual", "code_bge") is False

        settings.dual_code_bgem3_enabled = True
        assert indexing._embed_enabled_for("unified", "code_bge") is False  # unified 不用 code_bge

        monkeypatch.setattr(embedding_client, "enabled", lambda: False)
        assert indexing._embed_enabled_for("dual", "code_bge") is False      # 无 BGE key
    finally:
        settings.dual_code_bgem3_enabled = saved


def test_delete_dual_code_also_deletes_code_bge_mirror(monkeypatch):
    """dual 删 code 时对称删 code_vectors_bge 镜像；总开关关时只删 code。"""
    deleted: list[tuple] = []
    monkeypatch.setattr(
        milvus_client, "delete_vectors",
        lambda strategy, kind, ids: (deleted.append((strategy, kind, list(ids))), len(ids))[1],
    )
    saved = settings.dual_code_bgem3_enabled
    try:
        settings.dual_code_bgem3_enabled = True
        assert indexing.delete_chunks_from_milvus("dual", "code", ["c1", "c2"]) is True
        assert ("dual", "code", ["c1", "c2"]) in deleted
        assert ("dual", "code_bge", ["c1", "c2"]) in deleted

        deleted.clear()
        settings.dual_code_bgem3_enabled = False
        assert indexing.delete_chunks_from_milvus("dual", "code", ["c1"]) is True
        assert deleted == [("dual", "code", ["c1"])]   # 关 flag → 不删镜像
    finally:
        settings.dual_code_bgem3_enabled = saved


def test_delete_code_bge_failure_does_not_taint_primary(monkeypatch):
    """镜像删除失败仅记日志，不染主删返回值（仍 True）。"""
    def fake_delete(strategy, kind, ids):
        if kind == "code_bge":
            raise RuntimeError("bge down")
        return len(ids)

    monkeypatch.setattr(milvus_client, "delete_vectors", fake_delete)
    saved = settings.dual_code_bgem3_enabled
    try:
        settings.dual_code_bgem3_enabled = True
        assert indexing.delete_chunks_from_milvus("dual", "code", ["c1"]) is True
    finally:
        settings.dual_code_bgem3_enabled = saved


def test_reindex_code_bge_skips_when_disabled(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("disabled path must not touch DB or clients")

    monkeypatch.setattr(indexing, "_embed_enabled_for", lambda strategy, kind: False)
    monkeypatch.setattr(indexing, "_load_all_code_chunks", boom)
    monkeypatch.setattr(indexing, "index_chunks_to_milvus", boom)

    res = indexing.reindex_code_bge(_FakeSession(), strategy="dual")
    assert res["skipped"] is True and res["synced"] == 0


def test_reindex_code_bge_embeds_all_code(monkeypatch, reset_batch):
    """启用时按批 route 全部代码 chunk 到 code_bge（PK upsert 幂等）。"""
    settings.embed_batch_size = 2
    loaded = [{"chunk_id": f"c{i}", "text": "t"} for i in range(3)]   # 3 行 → 2 批 (2,1)
    routed: list[tuple] = []

    monkeypatch.setattr(indexing, "_embed_enabled_for", lambda strategy, kind: True)
    monkeypatch.setattr(indexing, "_load_all_code_chunks", lambda session, limit: loaded)
    monkeypatch.setattr(
        indexing, "index_chunks_to_milvus",
        lambda strategy, kind, batch: (routed.append((kind, len(batch))), True)[1],
    )

    res = indexing.reindex_code_bge(_FakeSession(), strategy="dual")
    assert res == {"total": 3, "synced": 3, "failed": 0, "skipped": False}
    assert all(kind == "code_bge" for kind, _ in routed)
    assert [n for _, n in routed] == [2, 1]


def test_reindex_code_bge_counts_failures(monkeypatch, reset_batch):
    settings.embed_batch_size = 2
    monkeypatch.setattr(indexing, "_embed_enabled_for", lambda strategy, kind: True)
    monkeypatch.setattr(
        indexing, "_load_all_code_chunks",
        lambda session, limit: [{"chunk_id": f"c{i}", "text": "t"} for i in range(4)],
    )
    monkeypatch.setattr(indexing, "index_chunks_to_milvus", lambda *a, **k: False)  # 全失败

    res = indexing.reindex_code_bge(_FakeSession(), strategy="dual")
    assert res["synced"] == 0 and res["failed"] == 4
