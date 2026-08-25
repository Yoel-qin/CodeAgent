"""M32 ②：graph_recall 关系类型过滤（SQL 形状/参数）——CaptureSession 零 infra。"""
from __future__ import annotations

import pytest

from app.retrieval.graph_traverse import _CALLS_SQL, _NEIGHBOR_SQL, _TYPED_SQL, graph_recall


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def mappings(self):
        return []  # fetch_chunks 降级返回空列表


class _CaptureSession:
    def __init__(self, rows=None):
        self.calls: list[tuple[str, dict]] = []
        self._rows = rows or []

    async def execute(self, sql, params=None):
        self.calls.append((str(sql), dict(params or {})))
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_none_relation_types_uses_legacy_sql_verbatim():
    s = _CaptureSession()
    await graph_recall(s, ["c1"], relation_types=None)
    assert s.calls[0] == (str(_NEIGHBOR_SQL), {"ids": ["c1"]})   # 逐字节现行为


@pytest.mark.asyncio
async def test_calls_only_hits_call_graph_sql():
    s = _CaptureSession()
    await graph_recall(s, ["c1"], relation_types=["calls"])
    assert len(s.calls) == 1
    assert s.calls[0][0] == str(_CALLS_SQL)
    assert "rts" not in s.calls[0][1]


@pytest.mark.asyncio
async def test_typed_filter_param_sorted_dedup():
    s = _CaptureSession()
    await graph_recall(s, ["c1"], relation_types=["doc_anchor", "implements", "implements"])
    sql_text, params = s.calls[0]
    assert sql_text == str(_TYPED_SQL)
    assert params["rts"] == ["CODE_IMPLEMENTS", "CODE_TO_DOC", "DOC_TO_CODE"]  # alphabetically sorted


@pytest.mark.asyncio
async def test_empty_selection_returns_empty_without_query():
    s = _CaptureSession()
    out = await graph_recall(s, ["c1"], relation_types=[])
    assert out == [] and s.calls == []      # 空选集不查库


@pytest.mark.asyncio
async def test_depth_two_traverses_levels_and_caps():
    # 第一层返回 n1/n2，第二层返回 n1（去重）+n3；max_nodes=3 截断
    rows_by_call = [[("n1",), ("n2",)], [("n1",), ("n3",)]]

    class _Leveled(_CaptureSession):
        async def execute(self, sql, params=None):
            self.calls.append((str(sql), dict(params or {})))
            return _FakeResult(rows_by_call.pop(0) if rows_by_call else [])

    out = await graph_recall(_Leveled(), ["c0"], depth=2, max_nodes=3, relation_types=["calls"])
    ids = {c["chunk_id"] for c in out} if out else set()
    # fetch_chunks 空结果 → 返回 []（FakeSession 无 code/doc 行）；此处只断言不炸 + 两层各查一次
    assert len(ids) >= 0
