"""文档维护 Agent 工具逻辑单测（M13：detect_stale_docs / submit_proposal）。

复用 test_agent_tools 的假 session 模式（``_FakeSession`` 同结果集 / ``_SeqSession`` 按序），
新增 ``_DispatchSession``（按 SQL 关键字分发——``_detect_stale`` 发多次异构查询：解析/关系/标签/变更/fetch）。
无需 infra。
"""
from __future__ import annotations

import app.agent.tools.maintain_tools as mt
from app.agent.tools import formatting as fmt
from app.agent.tools.maintain_tools import _detect_stale, _validate_anchors

# ---- 假 session ----


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def __iter__(self):  # fetch_chunks 直接迭代 .mappings()（不调 .all()）
        return iter(self._rows)


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


class _DispatchSession:
    """按 SQL 文本关键字分发预置结果集（关键字按列表顺序、首个命中胜出；最具体者放前面）。"""

    def __init__(self, mapping):
        self._mapping = mapping  # list[(keyword, rows)]

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        for kw, rows in self._mapping:
            if kw in sql:
                return _Result(rows)
        return _Result([])


# ---- formatting ----


def test_format_stale_candidates_populated():
    rows = [{"relation_id": 5, "anchor_key": "Foo.run", "code_label": "Foo.run",
             "doc_heading": "指南 › 3.2", "last_change": None}]
    out = fmt.format_stale_candidates(rows, "class:Foo")
    assert "找到 1 个 class:Foo 的文档-代码锚点" in out
    assert "relation_id=5" in out and "Foo.run" in out and "指南 › 3.2" in out
    assert "无变更记录" in out  # last_change=None 降级


def test_format_stale_candidates_with_change():
    rows = [{"relation_id": 5, "anchor_key": "Foo.run", "code_label": "Foo.run",
             "doc_heading": "指南", "last_change": {"change_type": "MODIFIED",
             "commit_message": "refactor run"}}]
    out = fmt.format_stale_candidates(rows, "class:Foo")
    assert "[MODIFIED]" in out and "refactor run" in out


def test_format_stale_candidates_empty():
    assert "未找到 class:Foo 的文档-代码锚点" in fmt.format_stale_candidates([], "class:Foo")


# ---- _detect_stale ----


async def test_detect_stale_full_path_assembles_candidates_and_chunks():
    mapping = [
        ("FROM change_history", []),  # staleness 证据（样本库无变更记录）
        ("FROM chunk_relations", [{"relation_id": 5, "anchor_key": "Foo.run",
                                   "source_chunk_id": "doc_y", "target_chunk_id": "code_Foo_run_abc12345",
                                   "relation_type": "DOC_TO_CODE", "confidence": 0.9}]),
        # fetch（须在 labels 前——fetch 的 SELECT 列含 content）
        ("content, heading_path FROM doc_chunks",
         [{"chunk_id": "doc_y", "content": "doc 文本", "heading_path": ["指南", "3.2"]}]),
        ("content, class_name, method_name FROM code_chunks",
         [{"chunk_id": "code_Foo_run_abc12345", "content": "void run(){}",
           "class_name": "Foo", "method_name": "run"}]),
        # 标签
        ("heading_path FROM doc_chunks WHERE chunk_id",
         [{"chunk_id": "doc_y", "heading_path": ["指南", "3.2"]}]),
        ("class_name, method_name FROM code_chunks WHERE chunk_id",
         [{"chunk_id": "code_Foo_run_abc12345", "class_name": "Foo", "method_name": "run"}]),
        # 解析 center（class: 前缀）
        ("is_deleted=false AND class_name",
         [{"chunk_id": "code_Foo_run_abc12345"}]),
    ]
    rows, chunks = await _detect_stale("class:Foo", _DispatchSession(mapping))
    assert len(rows) == 1
    r = rows[0]
    assert r["relation_id"] == 5
    assert r["relation_type"] == "DOC_TO_CODE"
    assert r["code_chunk_id"] == "code_Foo_run_abc12345" and r["doc_chunk_id"] == "doc_y"
    assert r["code_label"] == "Foo.run" and r["doc_heading"] == "指南 › 3.2"
    assert r["last_change"] is None  # 无变更记录
    assert len(chunks) == 2  # code + doc 两侧 chunk（供 citation）
    assert all(c["score"] == 0.6 for c in chunks)


async def test_detect_stale_no_code_returns_empty():
    # class:Unknown → 解析不到 code chunk → 短路（[], []），只发 1 次解析查询
    rows, chunks = await _detect_stale("class:Unknown", _FakeSession([]))
    assert rows == [] and chunks == []


async def test_detect_stale_no_relations_returns_empty():
    # 解析到 code chunk 但无锚点关系 → ([], [])，不发 fetch
    session = _DispatchSession([("is_deleted=false AND class_name", [{"chunk_id": "code_x"}])])
    rows, chunks = await _detect_stale("class:Foo", session)
    assert rows == [] and chunks == []


async def test_detect_stale_docs_tool_emits_citations(monkeypatch):
    pushed: list[dict] = []
    monkeypatch.setattr(mt, "get_stream_writer", lambda: lambda d: pushed.append(d))
    monkeypatch.setattr(mt, "_citation", lambda c: {"type": c.get("kind"), "chunk_id": c["chunk_id"]})
    mapping = [
        ("FROM change_history", []),
        ("FROM chunk_relations", [{"relation_id": 5, "anchor_key": "Foo.run",
                                   "source_chunk_id": "doc_y", "target_chunk_id": "code_x",
                                   "relation_type": "CODE_TO_DOC", "confidence": 0.8}]),
        ("content, heading_path FROM doc_chunks",
         [{"chunk_id": "doc_y", "content": "d", "heading_path": ["g"]}]),
        ("content, class_name, method_name FROM code_chunks",
         [{"chunk_id": "code_x", "content": "c", "class_name": "Foo", "method_name": "run"}]),
        ("heading_path FROM doc_chunks WHERE chunk_id",
         [{"chunk_id": "doc_y", "heading_path": ["g"]}]),
        ("class_name, method_name FROM code_chunks WHERE chunk_id",
         [{"chunk_id": "code_x", "class_name": "Foo", "method_name": "run"}]),
        ("is_deleted=false AND class_name", [{"chunk_id": "code_x"}]),
    ]
    out = await mt.detect_stale_docs.ainvoke(
        {"center": "class:Foo"}, {"configurable": {"session": _DispatchSession(mapping)}},
    )
    events = [p["event"] for p in pushed]
    assert "citation" in events and "agent_step" in events  # 内容工具：citation + step
    step = next(p["data"] for p in pushed if p["event"] == "agent_step")
    assert step["tool"] == "detect_stale_docs" and step["args"] == {"center": "class:Foo"} and step["n"] == 1
    assert isinstance(out, str) and "relation_id=5" in out


# ---- _validate_anchors ----


async def test_validate_anchors_returns_db_rows():
    rows = [{"relation_id": 5, "anchor_key": "Foo.run", "source_chunk_id": "doc_y",
             "target_chunk_id": "code_x", "relation_type": "DOC_TO_CODE"}]
    # DB 侧已按 relation_type/is_stale 过滤；函数只透传（+ 入参去重/转 int）
    out = await _validate_anchors([5, 5, 8], _FakeSession(rows))
    assert out == rows


async def test_validate_anchors_empty_and_bad_input():
    assert await _validate_anchors([], _FakeSession([])) == []          # 空入参短路
    assert await _validate_anchors(["x"], _FakeSession([])) == []       # 非整数 → 空（不查库）


async def test_submit_proposal_tool_valid_pushes_event(monkeypatch):
    pushed: list[dict] = []
    monkeypatch.setattr(mt, "get_stream_writer", lambda: lambda d: pushed.append(d))
    anchor = {"relation_id": 5, "anchor_key": "Foo.run", "source_chunk_id": "doc_y",
              "target_chunk_id": "code_x", "relation_type": "DOC_TO_CODE"}

    out = await mt.submit_proposal.ainvoke(
        {"summary": "Foo.run 文档过时", "relation_ids": [5], "reason": "代码已改"},
        {"configurable": {"session": _FakeSession([anchor])}},
    )
    events = [p["event"] for p in pushed]
    assert mt._PROPOSAL_EVENT in events           # 推了内部协议事件
    assert "agent_step" in events                 # + 轨迹步
    cap = next(p["data"] for p in pushed if p["event"] == mt._PROPOSAL_EVENT)
    assert cap["summary"] == "Foo.run 文档过时" and cap["anchors"] == [anchor]
    assert "已提交提案" in out and "Foo.run" in out


async def test_submit_proposal_tool_invalid_returns_error_no_event(monkeypatch):
    pushed: list[dict] = []
    monkeypatch.setattr(mt, "get_stream_writer", lambda: lambda d: pushed.append(d))

    out = await mt.submit_proposal.ainvoke(
        {"summary": "x", "relation_ids": [999], "reason": "r"},
        {"configurable": {"session": _FakeSession([])}},  # 校验全失败
    )
    events = [p["event"] for p in pushed]
    assert mt._PROPOSAL_EVENT not in events       # 无效 → 不推提案事件
    assert events == ["agent_step"]               # 仅一条 n=0 轨迹
    assert "无效" in out                           # 错误观察，提示重新 detect
