"""M32 ③：crosslink_recall（DOC↔CODE 锚点边双向扩展 + 多锚共识打分）——CaptureSession 零 infra。"""
from __future__ import annotations

import pytest

from app.retrieval.crosslink_search import crosslink_recall


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _MappersResult:
    def __init__(self, mappings_rows):
        self._m = mappings_rows

    def mappings(self):
        return list(self._m)


class _Session:
    """edges 行走 .all()；fetch_chunks 的 code/doc 查询走 .mappings()。"""

    def __init__(self, edge_rows=(), code_rows=(), doc_rows=()):
        self._edge = list(edge_rows)
        self._code = list(code_rows)
        self._doc = list(doc_rows)
        self.calls: list[str] = []

    async def execute(self, sql, params=None):
        t = str(sql)
        self.calls.append(t)
        if "chunk_relations" in t:
            return _FakeResult(self._edge)
        if "code_chunks" in t:
            return _MappersResult(self._code)
        return _MappersResult(self._doc)


@pytest.mark.asyncio
async def test_empty_seeds_returns_empty_without_query():
    s = _Session()
    assert await crosslink_recall(s, []) == []
    assert s.calls == []


@pytest.mark.asyncio
async def test_multi_seed_consensus_ranking_and_seed_exclusion():
    # d1 被 2 个种子链接（共识第一）、d2 被 1 个；s1 是种子自身 → 排除
    s = _Session(
        edge_rows=[("d1",), ("d2",), ("d1",), ("s1",)],
        code_rows=[{"chunk_id": "d1", "content": "a", "class_name": None, "method_name": None}],
        doc_rows=[{"chunk_id": "d2", "content": "b", "heading_path": []}],
    )
    out = await crosslink_recall(s, ["s1", "s2", "s3"], top_k=10)
    ids = [c["chunk_id"] for c in out]
    assert ids == ["d1", "d2"]          # 共识计数降序；d2 经 fetch_chunks 的 doc 分支返回


@pytest.mark.asyncio
async def test_allowed_kinds_post_filter():
    s = _Session(
        edge_rows=[("c1",), ("d1",)],
        code_rows=[{"chunk_id": "c1", "content": "a", "class_name": None, "method_name": None}],
        doc_rows=[{"chunk_id": "d1", "content": "b", "heading_path": []}],
    )
    out = await crosslink_recall(s, ["s"], allowed_kinds={"doc"})
    assert [c["chunk_id"] for c in out] == ["d1"]   # M45：无权 kind 不进候选
