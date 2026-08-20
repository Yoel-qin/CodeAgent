"""路径 A 向量召回单测（retrieval/vector_search.py）。

无 infra：monkeypatch ``query_embed`` / ``milvus_client.search`` / ``fetch_chunks``，验证 M25 dual
模式额外检索 ``code_vectors_bge``（用 BGE-M3 查询向量找回代码）、unified 逐字节不变、
flag/``doc`` 向量门控、空镜像 no-op。重点断言 **search 调用次数**（证明未吞 TypeError、top_k 位置参）。
"""
from __future__ import annotations

import pytest

from app.clients import embedding_client, milvus_client
from app.core.config import settings
from app.retrieval import vector_search


class _FakeSession:
    async def execute(self, *a, **k):
        return []


@pytest.fixture
def reset_strategy_flag():
    saved_strat = settings.embedding_strategy
    saved_flag = settings.dual_code_bgem3_enabled
    yield
    settings.embedding_strategy = saved_strat
    settings.dual_code_bgem3_enabled = saved_flag


def _setup(monkeypatch, *, query_vecs, search_by_kind, fetch_rows=None):
    """装假 query_embed / milvus.search / fetch_chunks；返回 search 调用记录列表 [(kind, vec, top_k)]。"""
    async def fake_query_embed(query):
        return query_vecs

    monkeypatch.setattr(embedding_client, "query_embed", fake_query_embed)

    calls: list[tuple] = []

    def fake_search(strategy, kind, vec, top_k, expr_kinds=None):
        calls.append((kind, vec, top_k))
        return [{"chunk_id": cid, "kind": kind, "score": 0.9} for cid in search_by_kind.get(kind, [])]

    monkeypatch.setattr(milvus_client, "search", fake_search)

    async def fake_fetch(session, ids):
        out = []
        for cid in ids:
            r = (fetch_rows or {}).get(cid, {})
            out.append({"chunk_id": cid, "kind": r.get("kind"), "content": r.get("content", "")})
        return out

    monkeypatch.setattr(vector_search, "fetch_chunks", fake_fetch)
    return calls


async def test_dual_adds_code_bge_search(monkeypatch, reset_strategy_flag):
    """M25：dual + flag 开 + doc 向量在 → 额外检索 code_bge（共 3 次 search）。"""
    settings.embedding_strategy = "dual"
    settings.dual_code_bgem3_enabled = True
    calls = _setup(
        monkeypatch,
        query_vecs={"code": [0.1] * 768, "doc": [0.2] * 1024},
        search_by_kind={"code": ["code_a"], "doc": ["doc_b"], "code_bge": ["code_c"]},
        fetch_rows={"code_a": {"kind": "code"}, "doc_b": {"kind": "doc"}, "code_c": {"kind": "code"}},
    )

    out = await vector_search.vector_recall(_FakeSession(), "查询", top_k=10)

    assert [c[0] for c in calls] == ["code", "doc", "code_bge"]   # 第 3 次是 M25 镜像检索
    assert all(c[2] == 10 for c in calls)                          # top_k 位置参（kw-only 陷阱未触发）
    assert {c["chunk_id"] for c in out} == {"code_a", "doc_b", "code_c"}


async def test_dual_skips_code_bge_when_flag_off(monkeypatch, reset_strategy_flag):
    settings.embedding_strategy = "dual"
    settings.dual_code_bgem3_enabled = False
    calls = _setup(
        monkeypatch,
        query_vecs={"code": [0.1] * 768, "doc": [0.2] * 1024},
        search_by_kind={"code": ["code_a"], "doc": ["doc_b"]},
    )
    await vector_search.vector_recall(_FakeSession(), "q", top_k=10)
    assert [c[0] for c in calls] == ["code", "doc"]   # 不检索 code_bge


async def test_dual_skips_code_bge_when_doc_vec_none(monkeypatch, reset_strategy_flag):
    settings.embedding_strategy = "dual"
    settings.dual_code_bgem3_enabled = True
    calls = _setup(
        monkeypatch,
        query_vecs={"code": [0.1] * 768, "doc": None},   # BGE-M3 查询向量不可用
        search_by_kind={"code": ["code_a"]},
    )
    await vector_search.vector_recall(_FakeSession(), "q", top_k=10)
    assert [c[0] for c in calls] == ["code"]   # 无 BGE 向量 → 不检索 code_bge


async def test_unified_unchanged_no_code_bge(monkeypatch, reset_strategy_flag):
    """unified 逐字节不变：仅 1 次 search（kind=None 混检），即便 flag 开也不触发 code_bge。"""
    settings.embedding_strategy = "unified"
    settings.dual_code_bgem3_enabled = True
    calls = _setup(
        monkeypatch,
        query_vecs={"unified": [0.1] * 1024},
        search_by_kind={None: ["code_a", "doc_b"]},
        fetch_rows={"code_a": {"kind": "code"}, "doc_b": {"kind": "doc"}},
    )
    out = await vector_search.vector_recall(_FakeSession(), "q", top_k=10)
    assert [c[0] for c in calls] == [None]
    assert {c["chunk_id"] for c in out} == {"code_a", "doc_b"}


async def test_dual_empty_code_bge_is_noop(monkeypatch, reset_strategy_flag):
    """code_vectors_bge 空（未 reindex）→ search 返 []，不报错，仍返 code+doc。"""
    settings.embedding_strategy = "dual"
    settings.dual_code_bgem3_enabled = True
    calls = _setup(
        monkeypatch,
        query_vecs={"code": [0.1] * 768, "doc": [0.2] * 1024},
        search_by_kind={"code": ["code_a"], "doc": ["doc_b"], "code_bge": []},
        fetch_rows={"code_a": {"kind": "code"}, "doc_b": {"kind": "doc"}},
    )
    out = await vector_search.vector_recall(_FakeSession(), "q", top_k=10)
    assert [c[0] for c in calls] == ["code", "doc", "code_bge"]   # 仍调（返空）
    assert {c["chunk_id"] for c in out} == {"code_a", "doc_b"}     # 无新增
