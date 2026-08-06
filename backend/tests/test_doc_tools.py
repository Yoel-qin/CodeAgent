"""文档问答 Agent 工具逻辑单测（mock 检索层 + 假 session，无需 infra）。"""
from __future__ import annotations

import app.agent.tools.doc_tools as dt
from app.agent.tools import formatting as fmt
from app.agent.tools.doc_tools import _get_related_code, _read_doc, _search_docs
from app.schemas.graph import GraphNode, GraphResponse

# ---- formatting ----


def test_format_doc_candidates():
    out = fmt.format_doc_candidates([
        {"chunk_id": "d1", "heading_path": ["架构", "向量召回"], "content": "xxx"},
    ])
    assert "架构 › 向量召回" in out and "d1" in out and "xxx" in out


def test_format_doc_candidates_empty():
    assert "未检索" in fmt.format_doc_candidates([])


def test_format_doc_detail_table():
    out = fmt.format_doc_detail({
        "chunk_id": "dt", "heading_path": ["配置"], "content": "表内容",
        "chunk_content_type": "table", "table_total_rows": 3, "table_total_cols": 4,
        "table_description": "参数表", "title": "设计文档", "file_path": "d.md",
    })
    assert "配置" in out and "表格 3×4" in out and "参数表" in out and "表内容" in out


def test_format_related_code():
    resp = GraphResponse(
        nodes=[GraphNode(id="c1", name="A.m", type="method"),
               GraphNode(id="doc_x", name="某文档", type="doc")],
        edges=[], center="doc_x",
    )
    out = fmt.format_related_code(resp)
    assert "A.m" in out and "c1" in out
    assert "某文档" not in out  # doc 节点不计入关联代码


# ---- _search_docs（mock pipeline.recall 返回混合 → 只留 doc）----


async def test_search_docs_filters_to_doc(monkeypatch):
    ranked = [
        {"chunk_id": "c1", "kind": "code", "content": "src", "score": 0.9},
        {"chunk_id": "d1", "kind": "doc", "content": "doc", "heading_path": ["a"], "score": 0.8},
        {"chunk_id": "d2", "kind": "doc", "content": "doc2", "score": 0.7},
    ]
    meta = {"recall": {"vector": 2, "lexical": 1, "graph": 0}, "fine": 2}

    async def fake_recall(session, query, **kw):
        return (ranked, meta)

    monkeypatch.setattr("app.retrieval.pipeline.pipeline.recall", fake_recall)
    res = await _search_docs("q", session=None, top_k=8)
    assert [c["chunk_id"] for c in res.chunks] == ["d1", "d2"]  # 只留 doc
    assert all(c["kind"] == "doc" for c in res.chunks)
    assert isinstance(res.chunks[0]["score"], float)
    assert "向量 2" in res.text


# ---- _read_doc（假 session.execute）----


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._m = _Mappings(rows)

    def mappings(self):
        return self._m


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt, params=None):
        return _Result(self._rows)


def _doc_row(**over):
    base = {"chunk_id": "d1", "content": "回查默认 15 次", "heading_path": ["事务", "回查"],
            "heading_level": 2, "section_order": 3, "chunk_content_type": "text",
            "page_number": None, "image_description": None, "image_caption": None,
            "table_data": None, "table_description": None, "table_total_rows": None,
            "table_total_cols": None, "context_before": None, "context_after": None,
            "linked_code_ids": None, "keywords": None, "file_path": "design.md",
            "title": "设计文档", "doc_type": "markdown"}
    base.update(over)
    return base


async def test_read_doc_text():
    res = await _read_doc("d1", _FakeSession([_doc_row()]))
    assert res.chunks[0]["chunk_id"] == "d1"
    assert res.chunks[0]["score"] == 1.0 and res.chunks[0]["kind"] == "doc"
    assert "事务 › 回查" in res.text and "回查默认 15 次" in res.text and "设计文档" in res.text


async def test_read_doc_table():
    row = _doc_row(chunk_id="dt", chunk_content_type="table", table_total_rows=3,
                   table_total_cols=4, table_description="参数表", content="表内容")
    res = await _read_doc("dt", _FakeSession([row]))
    assert "表格 3×4" in res.text and "参数表" in res.text


async def test_read_doc_missing():
    res = await _read_doc("nope", _FakeSession([]))
    assert res.chunks == []
    assert "未找到" in res.text


# ---- _get_related_code（mock get_code_doc_relations + fetch_chunks）----


async def test_get_related_code(monkeypatch):
    resp = GraphResponse(
        nodes=[GraphNode(id="doc_x", name="某文档", type="doc"),
               GraphNode(id="c1", name="A.m", type="method"),
               GraphNode(id="class:Foo", name="Foo", type="class")],
        edges=[], center="doc_x",
    )

    async def fake_rel(session, center, *, depth=1, max_nodes=30):
        return resp

    async def fake_fetch(session, ids):
        return [{"chunk_id": "c1", "kind": "code", "content": "s1", "class_name": "A", "method_name": "m"}]

    monkeypatch.setattr("app.services.graph_service.get_code_doc_relations", fake_rel)
    monkeypatch.setattr("app.agent.tools.doc_tools.fetch_chunks", fake_fetch)
    res = await _get_related_code("doc_x", session=None)
    assert [c["chunk_id"] for c in res.chunks] == ["c1"]  # 排除 doc_ 与 class:
    assert "A.m" in res.text


# ---- @tool 包装：citation/agent_step 事件推送 ----


async def test_search_docs_tool_emits_citations(monkeypatch):
    pushed: list[dict] = []
    monkeypatch.setattr(dt, "get_stream_writer", lambda: lambda d: pushed.append(d))
    monkeypatch.setattr(dt, "_citation", lambda c: {"type": c.get("kind"), "chunk_id": c["chunk_id"]})

    async def fake_recall(session, query, **kw):
        return ([{"chunk_id": "d1", "kind": "doc", "content": "s", "score": 0.9}],
                {"recall": {}, "fine": 1})

    monkeypatch.setattr("app.retrieval.pipeline.pipeline.recall", fake_recall)

    out = await dt.search_docs.ainvoke(
        {"query": "q"}, {"configurable": {"session": None, "top_k": 8}},
    )
    events = [p["event"] for p in pushed]
    assert "citation" in events and "agent_step" in events
    assert isinstance(out, str) and "d1" in out
