"""代码理解 Agent 工具逻辑单测（mock 检索层 + 假 session，无需 infra）。"""
from __future__ import annotations

from datetime import datetime

import app.agent.tools.code_tools as ct
from app.agent.tools import formatting as fmt
from app.agent.tools.code_tools import (
    _get_affected_docs,
    _get_call_chain,
    _get_callers,
    _get_downstream_callers,
    _get_existing_tests,
    _get_metrics,
    _get_recent_changes,
    _read_code,
    _rerank,
    _search_code,
    _search_symbol,
)
from app.schemas.graph import GraphEdge, GraphNode, GraphResponse, GraphSearchItem, GraphSearchResponse

# ---- formatting ----


def test_format_candidates():
    out = fmt.format_code_candidates([
        {"chunk_id": "c1", "class_name": "A", "method_name": "m", "content": "src"},
    ])
    assert "A.m" in out and "c1" in out and "src" in out


def test_format_candidates_empty():
    assert "未检索" in fmt.format_code_candidates([])


def test_format_call_graph():
    resp = GraphResponse(
        nodes=[GraphNode(id="c1", name="A.m", type="method"), GraphNode(id="c2", name="B.n", type="method")],
        edges=[GraphEdge(source="c1", target="c2", type="CALLS")],
        center="c1",
    )
    out = fmt.format_call_graph(resp, "BOTH")
    assert "A.m" in out and "B.n" in out and "调用" in out


def test_format_impact_callers():
    resp = GraphResponse(
        nodes=[GraphNode(id="c1", name="A.m", type="method", depth=0, class_name="A"),
               GraphNode(id="c2", name="B.n", type="method", depth=1, class_name="B"),
               GraphNode(id="c3", name="C.k", type="method", depth=2, class_name="C")],
        edges=[], center="c1",
    )
    out = fmt.format_impact_callers(resp)
    assert "A.m" in out and "B.n" in out and "C.k" in out   # 节点名
    assert "直接调用方" in out and "间接调用方" in out        # 按层归类
    assert "涉及" in out and "B" in out                     # 涉及类汇总


def test_format_impact_callers_caps_per_depth():
    # 同层超过 _MAX_PER_DEPTH(8) 个 → 只列前 8 + "还有 N 个"（防广泛被调方法爆 token，其余仍作引用）
    nodes = [GraphNode(id="c0", name="T.run", type="method", depth=0, class_name="T")]
    nodes += [GraphNode(id=f"x{i}", name=f"C{i}.m", type="method", depth=1, class_name=f"C{i}")
              for i in range(12)]
    out = fmt.format_impact_callers(GraphResponse(nodes=nodes, edges=[], center="c0"))
    assert "还有 4 个" in out   # 12 - 8 = 4 被折叠
    assert "x7" in out          # 前 8 个仍列出
    assert "x11" not in out     # 第 9~12 个不展开


# ---- _search_code（monkeypatch pipeline.recall）----


async def test_search_code(monkeypatch):
    candidates = [{"chunk_id": "c1", "kind": "code", "content": "src", "class_name": "A",
                   "method_name": "m", "score": 0.9}]
    meta = {"recall": {"vector": 1, "lexical": 0, "graph": 0}, "fine": 1}

    async def fake_recall(session, query, **kw):
        return (candidates, meta)

    monkeypatch.setattr("app.retrieval.pipeline.pipeline.recall", fake_recall)
    res = await _search_code("q", session=None, top_k=8)
    assert res.chunks[0]["chunk_id"] == "c1"
    assert isinstance(res.chunks[0]["score"], float)  # 归一为 float
    assert "向量 1" in res.text


# ---- _search_symbol（monkeypatch graph_service.search_graph_nodes）----


async def test_search_symbol(monkeypatch):
    resp = GraphSearchResponse(items=[
        GraphSearchItem(id="class:Foo", name="Foo", type="class"),
        GraphSearchItem(id="c9", name="Foo.bar", type="method", class_name="Foo"),
    ])

    async def fake_search(session, q, *, node_type=None, limit=10):
        return resp

    monkeypatch.setattr("app.services.graph_service.search_graph_nodes", fake_search)
    res = await _search_symbol("Foo", session=None)
    # 仅 method 项进 chunks（class:Foo 不可直接引用）
    assert [c["chunk_id"] for c in res.chunks] == ["c9"]
    assert "class:Foo" in res.text  # 但文本里都列出


# ---- _get_call_chain（monkeypatch get_call_graph + fetch_chunks）----


async def test_get_call_chain(monkeypatch):
    resp = GraphResponse(
        nodes=[GraphNode(id="c1", name="A.m", type="method", depth=0),
               GraphNode(id="c2", name="B.n", type="method", depth=1)],
        edges=[GraphEdge(source="c1", target="c2", type="CALLS")], center="c1",
    )

    async def fake_gcg(session, center, *, depth=2, direction="BOTH", max_nodes=30):
        return resp

    async def fake_fetch(session, ids):
        return [{"chunk_id": "c1", "kind": "code", "content": "s1", "class_name": "A", "method_name": "m"},
                {"chunk_id": "c2", "kind": "code", "content": "s2", "class_name": "B", "method_name": "n"}]

    monkeypatch.setattr("app.services.graph_service.get_call_graph", fake_gcg)
    monkeypatch.setattr("app.agent.tools.code_tools.fetch_chunks", fake_fetch)
    res = await _get_call_chain("c1", "BOTH", session=None, depth=2)
    assert {c["chunk_id"] for c in res.chunks} == {"c1", "c2"}
    # depth 越近分越高：c1(depth0) > c2(depth1)
    by = {c["chunk_id"]: c["score"] for c in res.chunks}
    assert by["c1"] > by["c2"]
    assert "A.m" in res.text and "B.n" in res.text


async def test_get_callers(monkeypatch):
    # 与 _get_call_chain 同构，但方向锁 CALLERS、max_nodes 放到 120（get_call_chain 是 30）
    resp = GraphResponse(
        nodes=[GraphNode(id="c1", name="A.m", type="method", depth=0),
               GraphNode(id="c2", name="B.n", type="method", depth=1)],
        edges=[], center="c1",
    )
    seen: dict = {}

    async def fake_gcg(session, center, *, depth=2, direction="BOTH", max_nodes=30):
        seen.update(direction=direction, max_nodes=max_nodes, depth=depth)
        return resp

    async def fake_fetch(session, ids):
        return [{"chunk_id": "c1", "kind": "code", "content": "s1", "class_name": "A", "method_name": "m"},
                {"chunk_id": "c2", "kind": "code", "content": "s2", "class_name": "B", "method_name": "n"}]

    monkeypatch.setattr("app.services.graph_service.get_call_graph", fake_gcg)
    monkeypatch.setattr("app.agent.tools.code_tools.fetch_chunks", fake_fetch)
    res = await _get_callers("c1", session=None, depth=3)
    assert seen["direction"] == "CALLERS" and seen["max_nodes"] == 120 and seen["depth"] == 3
    assert {c["chunk_id"] for c in res.chunks} == {"c1", "c2"}
    assert "调用方" in res.text and "A.m" in res.text  # format_impact_callers 输出


# ---- _read_code（假 session.execute）----


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


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


async def test_read_code():
    row = {"chunk_id": "c1", "content": "return 1;", "class_name": "A", "method_name": "m",
           "method_signature": "int m()", "start_line": 10, "end_line": 12,
           "javadoc": "does thing", "file_path": "A.java"}
    res = await _read_code("c1", _FakeSession([row]))
    assert res.chunks[0]["chunk_id"] == "c1" and res.chunks[0]["score"] == 1.0
    assert "int m()" in res.text and "does thing" in res.text and "return 1;" in res.text


async def test_read_code_missing():
    res = await _read_code("nope", _FakeSession([]))
    assert res.chunks == []
    assert "未找到" in res.text


# ---- @tool 包装：citiation/agent_step 事件推送（get_stream_writer 有上下文时）----


async def test_search_code_tool_emits_citations(monkeypatch):
    pushed: list[dict] = []
    monkeypatch.setattr(ct, "get_stream_writer", lambda: lambda d: pushed.append(d))
    monkeypatch.setattr(ct, "_citation",
                        lambda c: {"type": c.get("kind"), "chunk_id": c["chunk_id"]})

    async def fake_recall(session, query, **kw):
        return ([{"chunk_id": "c1", "kind": "code", "content": "s", "score": 0.9}],
                {"recall": {}, "fine": 1})

    monkeypatch.setattr("app.retrieval.pipeline.pipeline.recall", fake_recall)

    # 走 langchain 工具标准调用：ainvoke(args, config)，config 由框架注入到函数签名
    out = await ct.search_code.ainvoke(
        {"query": "q"}, {"configurable": {"session": None, "top_k": 8}},
    )
    events = [p["event"] for p in pushed]
    assert "citation" in events and "agent_step" in events  # 工具推了引用 + 步骤事件
    assert isinstance(out, str) and "c1" in out             # 返回给 LLM 的文本观察


async def test_get_callers_tool_emits_citations(monkeypatch):
    pushed: list[dict] = []
    monkeypatch.setattr(ct, "get_stream_writer", lambda: lambda d: pushed.append(d))
    monkeypatch.setattr(ct, "_citation", lambda c: {"type": c.get("kind"), "chunk_id": c["chunk_id"]})

    resp = GraphResponse(
        nodes=[GraphNode(id="c1", name="A.m", type="method", depth=0),
               GraphNode(id="c2", name="B.n", type="method", depth=1)],
        edges=[], center="c1")

    async def fake_gcg(session, center, *, depth=3, direction="CALLERS", max_nodes=120):
        return resp

    async def fake_fetch(session, ids):
        return [{"chunk_id": "c1", "kind": "code", "content": "s1", "class_name": "A", "method_name": "m"},
                {"chunk_id": "c2", "kind": "code", "content": "s2", "class_name": "B", "method_name": "n"}]

    monkeypatch.setattr("app.services.graph_service.get_call_graph", fake_gcg)
    monkeypatch.setattr("app.agent.tools.code_tools.fetch_chunks", fake_fetch)

    out = await ct.get_callers.ainvoke(
        {"center": "c1", "depth": 3}, {"configurable": {"session": None, "top_k": 8}},
    )
    events = [p["event"] for p in pushed]
    assert "citation" in events and "agent_step" in events
    assert isinstance(out, str) and "调用方" in out and "c1" in out


# ---- get_recent_changes（缺陷诊断回归排查工具；元数据工具，无 citation）----


def test_format_change_history_populated():
    rows = [
        {"change_type": "MODIFIED", "git_commit_hash": "a1b2c3d4e5", "git_commit_time": datetime(2026, 7, 28),
         "git_author": "alice", "commit_message": "fix: handle null in checkLocalTransaction"},
        {"change_type": "ADDED", "git_commit_hash": "f6e5d4c3b2", "git_commit_time": datetime(2026, 7, 20),
         "git_author": "bob", "commit_message": "add transaction check"},
    ]
    out = fmt.format_change_history(rows, "c1")
    assert "最近 2 次" in out
    assert "MODIFIED" in out and "alice" in out and "a1b2c3d4" in out   # 类型/作者/短 hash
    assert "fix: handle null" in out
    assert "2026-07-28" in out


def test_format_change_history_empty():
    # 全量入库、未经增量同步 → 无历史 → 提示文（优雅降级）
    out = fmt.format_change_history([], "c1")
    assert "无已记录的变更历史" in out and "增量同步" in out


async def test_get_recent_changes_logic():
    rows = [{"change_type": "MODIFIED", "git_commit_hash": "a1b2c3d4", "git_commit_time": datetime(2026, 7, 28),
             "git_author": "alice", "commit_message": "fix null"}]
    out = await _get_recent_changes("c1", _FakeSession(rows))
    assert isinstance(out, list) and len(out) == 1          # 返回原始行（dict 列表）
    assert out[0]["change_type"] == "MODIFIED"


async def test_get_recent_changes_tool_emits_step_only(monkeypatch):
    # 元数据工具：只发 agent_step（n=变更行数），不发 citation（不与 read_code 重复）
    pushed: list[dict] = []
    monkeypatch.setattr(ct, "get_stream_writer", lambda: lambda d: pushed.append(d))
    rows = [{"change_type": "MODIFIED", "git_commit_hash": "a1b2c3d4", "git_commit_time": datetime(2026, 7, 28),
             "git_author": "alice", "commit_message": "fix"}]

    out = await ct.get_recent_changes.ainvoke(
        {"chunk_id": "c1"}, {"configurable": {"session": _FakeSession(rows), "top_k": 8}},
    )
    events = [p["event"] for p in pushed]
    assert events == ["agent_step"]                          # 仅 step，无 citation
    step = pushed[0]["data"]
    assert step["tool"] == "get_recent_changes" and step["args"] == {"chunk_id": "c1"} and step["n"] == 1
    assert isinstance(out, str) and "MODIFIED" in out


async def test_get_recent_changes_tool_empty_history(monkeypatch):
    # 空历史 → step n=0 + 提示文，不中断
    pushed: list[dict] = []
    monkeypatch.setattr(ct, "get_stream_writer", lambda: lambda d: pushed.append(d))

    out = await ct.get_recent_changes.ainvoke(
        {"chunk_id": "c1"}, {"configurable": {"session": _FakeSession([]), "top_k": 8}},
    )
    assert pushed[0]["data"]["n"] == 0
    assert "无已记录的变更历史" in out


# ---- get_code_metrics（代码审查量化度量工具；元数据工具，无 citation）----


class _SeqSession:
    """按 execute 调用顺序依次返回不同结果集（_get_metrics 发 2 次查询：code_chunks 行 + call_graph 计数）。"""

    def __init__(self, result_sets):
        self._sets = list(result_sets)
        self._i = 0

    async def execute(self, stmt, params=None):
        rows = self._sets[min(self._i, len(self._sets) - 1)]
        self._i += 1
        return _Result(rows)


def test_format_code_metrics_populated():
    m = {"found": True, "chunk_id": "c1", "class_name": "A", "method_name": "m",
         "method_signature": "void m(int x)", "loc": 150, "token_count": 320,
         "fan_in": 20, "fan_out": 12}
    out = fmt.format_code_metrics(m)
    assert "LOC=150" in out and "fan-in=20" in out and "fan-out=12" in out
    assert "方法偏长" in out and "被广泛调用" in out and "耦合偏高" in out   # 三阈值全触发
    assert "void m(int x)" in out


def test_format_code_metrics_missing():
    out = fmt.format_code_metrics({"found": False, "chunk_id": "c1"})
    assert "未找到" in out and "c1" in out


async def test_get_metrics_logic():
    chunk_row = {"chunk_id": "c1", "class_name": "A", "method_name": "m",
                 "method_signature": "int m()", "start_line": 10, "end_line": 25,
                 "token_count": 120}
    count_row = {"fan_in": 3, "fan_out": 5}
    out = await _get_metrics("c1", _SeqSession([[chunk_row], [count_row]]))
    assert out["found"] is True
    assert out["loc"] == 16               # 25 - 10 + 1
    assert out["fan_in"] == 3 and out["fan_out"] == 5
    assert out["token_count"] == 120


async def test_get_metrics_missing():
    # code_chunks 查询为空即短路（不发第二次查询）
    out = await _get_metrics("nope", _SeqSession([[]]))
    assert out["found"] is False


async def test_get_code_metrics_tool_emits_step_only(monkeypatch):
    # 元数据工具：只发 agent_step（命中 n=1），不发 citation
    pushed: list[dict] = []
    monkeypatch.setattr(ct, "get_stream_writer", lambda: lambda d: pushed.append(d))
    chunk_row = {"chunk_id": "c1", "class_name": "A", "method_name": "m",
                 "method_signature": "int m()", "start_line": 10, "end_line": 25,
                 "token_count": 120}
    count_row = {"fan_in": 3, "fan_out": 5}

    out = await ct.get_code_metrics.ainvoke(
        {"chunk_id": "c1"}, {"configurable": {"session": _SeqSession([[chunk_row], [count_row]]), "top_k": 8}},
    )
    events = [p["event"] for p in pushed]
    assert events == ["agent_step"]                        # 仅 step，无 citation
    step = pushed[0]["data"]
    assert step["tool"] == "get_code_metrics" and step["args"] == {"chunk_id": "c1"} and step["n"] == 1
    assert isinstance(out, str) and "LOC=16" in out


# ---- get_existing_tests（测试生成 Agent 对齐项目测试约定工具；内容工具，发 citation）----


def test_format_existing_tests_populated():
    rows = [{"chunk_id": "c1", "class_name": "AccountTest", "method_name": "testFoo",
             "content": "@Test void testFoo(){ assertTrue(true); }"}]
    out = fmt.format_existing_tests(rows, "Account")
    assert "找到 1 个 Account 的现有测试" in out
    assert "AccountTest.testFoo" in out and "c1" in out          # 类.方法 + chunk_id
    assert "测试约定" in out                                       # 提示对齐约定


def test_format_existing_tests_empty():
    # 无现有测试 → 提示按通用约定生成（样本库常空，优雅降级）
    out = fmt.format_existing_tests([], "Account")
    assert "未找到 Account 的现有测试" in out
    assert "JUnit 5" in out and "Mockito" in out


async def test_get_existing_tests_logic():
    # center 为 chunk_id → 先解析 class_name（第 1 次查询），再 ILIKE '%Account%Test'（第 2 次）
    account_row = {"class_name": "Account"}
    test_rows = [
        {"chunk_id": "code_AccountTest_testFoo_abc12345", "class_name": "AccountTest",
         "method_name": "testFoo", "method_signature": "void testFoo()",
         "content": "@Test void testFoo(){}", "file_path": "AccountTest.java"},
        {"chunk_id": "code_AccountTest_testBar_def67890", "class_name": "AccountTest",
         "method_name": "testBar", "method_signature": "void testBar()",
         "content": "@Test void testBar(){}", "file_path": "AccountTest.java"},
    ]
    rows, cls = await _get_existing_tests("code_Account_withdraw_a1b2c3d4", _SeqSession([[account_row], test_rows]))
    assert cls == "Account"                                       # 从 chunk_id 解析出类名
    assert len(rows) == 2
    assert rows[0]["class_name"] == "AccountTest"


async def test_get_existing_tests_class_prefix():
    # center 为 class:Account → 剥前缀直接当类名，只发 1 次查询（无 chunk_id 解析）
    test_rows = [{"chunk_id": "c1", "class_name": "AccountTest", "method_name": "testFoo",
                  "content": "@Test void testFoo(){}", "file_path": "AccountTest.java"}]
    rows, cls = await _get_existing_tests("class:Account", _FakeSession(test_rows))
    assert cls == "Account"
    assert len(rows) == 1


async def test_get_existing_tests_missing():
    # 无测试类 → 空列表（class: 前缀只发 1 次查询，返回空）
    rows, cls = await _get_existing_tests("class:Account", _FakeSession([]))
    assert rows == [] and cls == "Account"


async def test_get_existing_tests_tool_emits_citations(monkeypatch):
    # 内容工具：发 citation（区别于 get_recent_changes/get_code_metrics 仅 step）+ agent_step
    pushed: list[dict] = []
    monkeypatch.setattr(ct, "get_stream_writer", lambda: lambda d: pushed.append(d))
    monkeypatch.setattr(ct, "_citation", lambda c: {"type": c.get("kind"), "chunk_id": c["chunk_id"]})
    test_rows = [{"chunk_id": "c1", "class_name": "AccountTest", "method_name": "testFoo",
                  "method_signature": "void testFoo()", "content": "@Test void testFoo(){}",
                  "file_path": "AccountTest.java"}]

    out = await ct.get_existing_tests.ainvoke(
        {"center": "class:Account"}, {"configurable": {"session": _FakeSession(test_rows), "top_k": 8}},
    )
    events = [p["event"] for p in pushed]
    assert "citation" in events and "agent_step" in events        # 内容工具：citation + step
    step = next(p["data"] for p in pushed if p["event"] == "agent_step")
    assert step["tool"] == "get_existing_tests" and step["args"] == {"center": "class:Account"} and step["n"] == 1
    assert isinstance(out, str) and "AccountTest" in out and "现有测试" in out


# ---- get_downstream_callers（下游被调用面；与 get_callers 对称，方向锁 CALLEES）----


def test_format_impact_callees():
    resp = GraphResponse(
        nodes=[GraphNode(id="c1", name="A.m", type="method", depth=0, class_name="A"),
               GraphNode(id="c2", name="B.n", type="method", depth=1, class_name="B")],
        edges=[], center="c1")
    out = fmt.format_impact_callees(resp)
    assert "下游被调用" in out and "直接依赖" in out and "A.m" in out and "B.n" in out


def test_format_impact_callees_empty():
    out = fmt.format_impact_callees(GraphResponse(nodes=[], edges=[], center="c1"))
    assert "无下游被调用" in out


async def test_get_downstream_callers(monkeypatch):
    # 与 _get_callers 同构，但方向锁 CALLEES、max_nodes=120、depth=3
    resp = GraphResponse(
        nodes=[GraphNode(id="c1", name="A.m", type="method", depth=0),
               GraphNode(id="c2", name="B.n", type="method", depth=1)],
        edges=[], center="c1")
    seen: dict = {}

    async def fake_gcg(session, center, *, depth=2, direction="BOTH", max_nodes=30):
        seen.update(direction=direction, max_nodes=max_nodes, depth=depth)
        return resp

    async def fake_fetch(session, ids):
        return [{"chunk_id": "c1", "kind": "code", "content": "s1", "class_name": "A", "method_name": "m"},
                {"chunk_id": "c2", "kind": "code", "content": "s2", "class_name": "B", "method_name": "n"}]

    monkeypatch.setattr("app.services.graph_service.get_call_graph", fake_gcg)
    monkeypatch.setattr("app.agent.tools.code_tools.fetch_chunks", fake_fetch)
    res = await _get_downstream_callers("c1", session=None, depth=3)
    assert seen["direction"] == "CALLEES" and seen["max_nodes"] == 120 and seen["depth"] == 3
    assert {c["chunk_id"] for c in res.chunks} == {"c1", "c2"}
    assert "下游" in res.text and "A.m" in res.text  # format_impact_callees 输出


# ---- get_affected_docs（锚定到代码的文档；带腐化信号，对接文档维护弧线）----


def test_format_affected_docs():
    rows = [{"chunk_id": "d1", "heading_path": ["事务"],
             "last_change": {"change_type": "MODIFIED", "git_commit_time": datetime(2026, 7, 28)}}]
    out = fmt.format_affected_docs(rows, "c1")
    assert "锚定" in out and "事务" in out and "MODIFIED" in out and "过时" in out
    assert "未找到锚定" in fmt.format_affected_docs([], "c1")


async def test_get_affected_docs_logic():
    # chunk_id 路径：2 次查询（doc_chunks linked_code_ids 重叠 → change_history 最近变更）
    doc_rows = [{"chunk_id": "doc_d1", "content": "事务回查说明", "heading_path": ["事务", "回查"]}]
    change_rows = [{"chunk_id": "c1", "change_type": "MODIFIED",
                    "git_commit_time": datetime(2026, 7, 28), "commit_message": "fix"}]
    res = await _get_affected_docs("c1", _SeqSession([doc_rows, change_rows]))
    assert res.chunks[0]["chunk_id"] == "doc_d1" and res.chunks[0]["kind"] == "doc"
    assert "锚定" in res.text and "事务 › 回查" in res.text
    assert "MODIFIED" in res.text and "2026-07-28" in res.text  # 代码最近变更腐化信号


async def test_get_affected_docs_none():
    res = await _get_affected_docs("c1", _SeqSession([[], []]))
    assert res.chunks == [] and "未找到锚定" in res.text


# ---- rerank（精排工具；元数据工具，仅 step 无 citation；无 key/失败降级原序）----


def test_format_rerank():
    out = fmt.format_rerank([
        {"chunk_id": "c1", "class_name": "A", "method_name": "m", "score": 0.9},
        {"chunk_id": "c2", "class_name": None, "method_name": None, "score": 0.1},
    ])
    assert "重排 2 个" in out and "A.m" in out and "c2" in out and "0.900" in out
    assert "无候选可重排" in fmt.format_rerank([])


async def test_rerank_reorders(monkeypatch):
    monkeypatch.setattr(ct.settings, "reranker_fine_model", "BAAI/test")

    async def fake_fetch(session, ids):
        return [{"chunk_id": "c1", "kind": "code", "content": "a", "class_name": "A", "method_name": "x"},
                {"chunk_id": "c2", "kind": "code", "content": "b", "class_name": "B", "method_name": "y"}]

    async def fake_rerank(query, candidates, *, model, top_n):
        return [{"chunk_id": "c2", "kind": "code", "content": "b", "score": 0.9},
                {"chunk_id": "c1", "kind": "code", "content": "a", "score": 0.1}]  # 翻转顺序

    monkeypatch.setattr("app.agent.tools.code_tools.fetch_chunks", fake_fetch)
    monkeypatch.setattr("app.agent.tools.code_tools.rerank_stage", fake_rerank)
    ranked, reranked = await _rerank("q", ["c1", "c2"], session=None)
    assert reranked is True
    assert [r["chunk_id"] for r in ranked] == ["c2", "c1"]


async def test_rerank_degrades_on_failure(monkeypatch):
    monkeypatch.setattr(ct.settings, "reranker_fine_model", "BAAI/test")

    async def fake_fetch(session, ids):
        return [{"chunk_id": "c1", "kind": "code", "content": "a"}]

    async def boom(*a, **k):
        raise RuntimeError("no reranker key")

    monkeypatch.setattr("app.agent.tools.code_tools.fetch_chunks", fake_fetch)
    monkeypatch.setattr("app.agent.tools.code_tools.rerank_stage", boom)
    ranked, reranked = await _rerank("q", ["c1"], session=None)
    assert reranked is False                                   # 降级：保持原序
    assert [r["chunk_id"] for r in ranked] == ["c1"]


async def test_rerank_tool_step_only(monkeypatch):
    # 元数据工具：只发 agent_step（n=候选数），不发 citation（chunks 已由先前检索工具引用过）
    pushed: list[dict] = []
    monkeypatch.setattr(ct, "get_stream_writer", lambda: lambda d: pushed.append(d))
    monkeypatch.setattr(ct.settings, "reranker_fine_model", "BAAI/test")

    async def fake_fetch(session, ids):
        return [{"chunk_id": "c1", "kind": "code", "content": "a", "class_name": "A", "method_name": "m"}]

    async def fake_rerank(query, candidates, *, model, top_n):
        return [{"chunk_id": "c1", "kind": "code", "content": "a", "score": 0.95}]

    monkeypatch.setattr("app.agent.tools.code_tools.fetch_chunks", fake_fetch)
    monkeypatch.setattr("app.agent.tools.code_tools.rerank_stage", fake_rerank)

    out = await ct.rerank.ainvoke(
        {"query": "q", "chunk_ids": ["c1"]}, {"configurable": {"session": None, "top_k": 8}},
    )
    assert [p["event"] for p in pushed] == ["agent_step"]      # 仅 step，无 citation
    step = pushed[0]["data"]
    assert step["tool"] == "rerank" and step["args"]["n"] == 1
    assert isinstance(out, str) and "重排" in out and "已按相关性重排" in out
