"""tools_loader：wrap 计步/引用提取/循环检测单测 + stdio 子进程集成（真 MCP 协议）。"""
import json
import os
import sys
from pathlib import Path

from langchain_core.tools import StructuredTool

from app.agent.tools_loader import (
    ToolCallTracker,
    _extract_citations,
    get_code_tools,
    get_doc_tools,
    load_tools,
    reset_tools,
    tools_ready,
    wrap_tool,
)

FIX = (Path(__file__).parent / "fixtures").resolve()

# 适配记录：langchain-core 1.6.1 起 StructuredTool.args_schema 是必填字段
# （不传 → pydantic ValidationError），故在 brief 契约的 _fake_tool 上补一个
# 透传 JSON-schema dict（dict schema 不做校验，kwargs 原样透传给 coroutine）。
_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}


def _fake_tool(name: str, result: dict) -> StructuredTool:
    async def _fn(**kwargs):
        return json.dumps(result, ensure_ascii=False)
    return StructuredTool(name=name, description="t", args_schema=_EMPTY_SCHEMA, coroutine=_fn)


# ── brief 契约测试（wrap 单测 2 个） ──────────────────────────────────────


async def test_wrap_emits_step_and_citation():
    tracker = ToolCallTracker()
    tool = wrap_tool(_fake_tool("grep_code", {"matches": [
        {"file": "a/CommitLog.java", "line": 10, "content": "x"}], "total_count": 1}), tracker)
    out = await tool.ainvoke({"pattern": "putMessage"})
    assert "CommitLog.java" in out
    assert tracker.steps[0]["tool"] == "grep_code" and tracker.steps[0]["n"] == 1
    assert tracker.citations == [{"kind": "code", "file_path": "a/CommitLog.java",
                                  "start_line": 10, "end_line": 10, "label": "a/CommitLog.java:10"}]


async def test_wrap_loop_detection_third_call():
    tracker = ToolCallTracker()
    tool = wrap_tool(_fake_tool("grep_code", {"matches": []}), tracker)
    args = {"pattern": "same"}
    # 适配记录：brief 原文用元组解包 `(await ... for _ in range(3))`，那是 async
    # generator、Python 不允许解包（TypeError）——改为列表推导，断言不变。
    r1, r2, r3 = [await tool.ainvoke(args) for _ in range(3)]
    assert "loop detected" in r3 and "loop detected" not in r1
    assert tracker.looped == ["grep_code"]


# ── wrap 补充：步骤形状 / 工具身份保留 / 第 4 次同参仍拦截 ─────────────────


async def test_wrap_step_shape_and_identity():
    tracker = ToolCallTracker()
    src = _fake_tool("read_file", {"content": "x", "total_lines": 1})
    tool = wrap_tool(src, tracker)
    assert tool.name == src.name and tool.description == src.description
    assert tool.args_schema == src.args_schema
    await tool.ainvoke({"file_path": "a/X.java", "start_line": 5})
    step = tracker.steps[0]
    assert step["args"] == {"file_path": "a/X.java", "start_line": 5}
    assert step["n"] == 1 and isinstance(step["duration_ms"], float) and step["duration_ms"] >= 0


async def test_wrap_loop_fourth_call_still_blocked_once_reported():
    tracker = ToolCallTracker()
    tool = wrap_tool(_fake_tool("grep_code", {"matches": []}), tracker)
    for _ in range(4):
        out = await tool.ainvoke({"pattern": "same"})
    assert "loop detected" in out
    assert tracker.looped == ["grep_code"]  # 只在第 3 次记一次，不重复膨胀


async def test_wrap_mcp_content_blocks_normalized():
    """MCP 工具 ainvoke 返回 content-block 列表（adapters 0.3.x 形状），wrap 需归一成字符串。"""
    tracker = ToolCallTracker()

    async def _blocks(**kwargs):
        # adapters 0.3.x 真实形状：coroutine 返回 (content_blocks, artifact) 二元组
        return ([{"type": "text", "text": json.dumps({"matches": [
            {"file": "a/B.java", "line": 3, "content": "y"}]})}], None)

    src = StructuredTool(name="grep_code", description="d", args_schema=_EMPTY_SCHEMA, coroutine=_blocks,
                         response_format="content_and_artifact")
    tool = wrap_tool(src, tracker)
    out = await tool.ainvoke({"pattern": "x"})
    assert "B.java" in out and tracker.citations[0]["file_path"] == "a/B.java"


# ── _extract_citations 分派表 ─────────────────────────────────────────────


def test_extract_grep_files_and_glob_modes_yield_nothing():
    assert _extract_citations("grep_code", {}, {"files": ["a/X.java"], "total_count": 1}) == []
    assert _extract_citations("grep_code", {}, {"counts": [{"file": "a/X.java", "count": 1}]}) == []
    assert _extract_citations("glob_files", {}, {"files": ["a/X.java"], "total_count": 1}) == []


def test_extract_read_file_window():
    c = _extract_citations("read_file", {"file_path": "a/X.java", "start_line": 5},
                           {"content": "x", "total_lines": 900, "start_line": 5, "end_line": 104, "truncated": True})
    assert c == [{"kind": "code", "file_path": "a/X.java", "start_line": 5, "end_line": 104, "label": "a/X.java:5-104"}]
    # 无 end_line → start+99
    c2 = _extract_citations("read_file", {"file_path": "a/Y.java"}, {"content": "x", "total_lines": 1})
    assert c2[0]["end_line"] == 100 and c2[0]["label"] == "a/Y.java:1-100"


def test_extract_find_symbol_method_label():
    res = {"locations": [
        {"file": "a/CommitLog.java", "line": 10, "content": "void putMessage()", "kind": "method"},
        {"file": "b/Other.java", "line": 2, "content": "class Other", "kind": "type"}]}
    c = _extract_citations("find_symbol", {"symbol_name": "putMessage"}, res)
    assert c[0] == {"kind": "code", "file_path": "a/CommitLog.java", "start_line": 10,
                    "end_line": 10, "label": "a/CommitLog.java#putMessage"}
    assert c[1]["label"] == "b/Other.java:2"


def test_extract_graph_edges():
    res = {"edges": [{"caller_class": "A", "callee_class": "B", "file": "a/A.java", "line": 7, "depth": 1},
                     {"caller_class": "C", "callee_class": "B", "file": None, "line": None, "depth": 1}]}
    for name in ("get_callees", "get_callers"):
        c = _extract_citations(name, {}, res)
        assert c == [{"kind": "code", "file_path": "a/A.java", "start_line": 7, "end_line": 7, "label": "a/A.java:7"}]


def test_extract_doc_tools():
    hits = {"results": [{"section_id": "s1", "doc_name": "overview.md", "title": "架构", "anchor": "arch", "score": 1.0}],
            "recall": 1}
    for name in ("doc_semantic_search", "doc_keyword_search", "doc_hybrid_search"):
        assert _extract_citations(name, {"query": "q"}, hits) == [
            {"kind": "doc", "doc_id": "overview.md", "section": "arch", "label": "overview.md#架构"}]
    sec = {"document_id": 3, "doc_name": "overview.md", "anchor": "arch", "title": "架构", "content": "c", "kind": "text"}
    assert _extract_citations("read_doc_section", {"doc_id": 3, "anchor": "arch"}, sec) == [
        {"kind": "doc", "doc_id": "overview.md", "section": "arch", "label": "overview.md#架构"}]
    toc = {"toc": [{"document_id": 3, "doc_name": "overview.md", "anchor": "arch", "title": "架构",
                    "level": 1, "order_index": 0}]}
    assert _extract_citations("get_doc_toc", {}, toc) == [
        {"kind": "doc", "doc_id": "overview.md", "section": "arch", "label": "overview.md#架构"}]


def test_extract_error_result_yields_nothing():
    assert _extract_citations("read_file", {"file_path": "x"}, {"error": "file not found"}) == []
    assert _extract_citations("get_callees", {}, {"error": "q", "edges": [], "total": 0}) == []
    assert _extract_citations("unknown_tool", {}, {"whatever": 1}) == []


def test_tracker_citation_dedup_and_cap():
    tracker = ToolCallTracker()
    matches = [{"file": f"a/F{i}.java", "line": i + 1} for i in range(25)]  # 行号 1-based
    # 去重键 (file_path, start_line)：25 个不同命中 → 25 条
    assert len(_extract_citations("grep_code", {}, {"matches": matches})) == 25
    # 合计截 20（超出丢弃不报错）
    tracker.add_citations(_extract_citations("grep_code", {}, {"matches": matches}))
    assert len(tracker.citations) == 20
    assert tracker.citations[0]["file_path"] == "a/F0.java"
    # 同 (file, line) 重复写入不增；已满 20 后再写也不再增
    dup = [{"kind": "code", "file_path": "a/F0.java", "start_line": 0, "end_line": 0, "label": "a/F0.java:0"},
           {"kind": "code", "file_path": "a/NEW.java", "start_line": 1, "end_line": 1, "label": "a/NEW.java:1"}]
    tracker.add_citations(dup)
    assert len(tracker.citations) == 20
    assert tracker.citations[0]["file_path"] == "a/F0.java"


async def test_wrap_accumulates_citations_across_calls_with_dedup():
    tracker = ToolCallTracker()
    tool = wrap_tool(_fake_tool("grep_code", {"matches": [{"file": "a/F.java", "line": 1}]}), tracker)
    await tool.ainvoke({"pattern": "one"})
    await tool.ainvoke({"pattern": "two"})  # 不同 args、相同命中 → 去重
    assert len(tracker.steps) == 2 and tracker.steps[1]["n"] == 2
    assert len(tracker.citations) == 1


# ── load_tools：stdio 集成 + fail-soft ───────────────────────────────────


def _stdio_transport(server: str) -> dict:
    env = {**os.environ, "REPOS_ROOT": str(FIX), "DEFAULT_REPO": "mini_repo", "EMBEDDING_API_KEY": ""}
    return {"command": sys.executable, "args": ["-m", f"app.mcp_servers.{server}_server", "--stdio"],
            "transport": "stdio", "env": env}


async def test_load_tools_stdio_code_server():
    reset_tools()
    try:
        await load_tools({"code": _stdio_transport("code")})
        assert tools_ready() is True
        assert {t.name for t in get_code_tools()} == {
            "grep_code", "glob_files", "read_file", "list_directory", "find_symbol"}
        assert get_doc_tools() == []  # 未提供的 server 不加载，不误报
    finally:
        reset_tools()
    assert tools_ready() is False and get_code_tools() == []


async def test_load_tools_fail_soft_on_dead_server():
    reset_tools()
    try:
        await load_tools({"code": {"command": "definitely-not-a-cmd-xyz", "args": [], "transport": "stdio"}})
        assert tools_ready() is False
        assert get_code_tools() == [] and get_doc_tools() == []
    finally:
        reset_tools()


async def test_load_tools_doc_server_via_stdio():
    reset_tools()
    try:
        await load_tools({"doc": _stdio_transport("doc")})
        assert {t.name for t in get_doc_tools()} == {
            "doc_semantic_search", "doc_keyword_search", "doc_hybrid_search", "read_doc_section", "get_doc_toc"}
        assert get_code_tools() == []
    finally:
        reset_tools()


async def test_load_tools_stdio_wrap_real_grep_call():
    """端到端：stdio 真工具 → wrap → 真实 MCP content-block 观测里提取 citation。"""
    reset_tools()
    try:
        await load_tools({"code": _stdio_transport("code")})
        tracker = ToolCallTracker()
        tool = wrap_tool(next(t for t in get_code_tools() if t.name == "grep_code"), tracker)
        out = await tool.ainvoke({"pattern": "MAX_RETRY_TIMES"})
        assert "CommitLog.java" in out
        assert tracker.steps[0]["tool"] == "grep_code" and tracker.steps[0]["n"] == 1
        assert any(c["file_path"].endswith("CommitLog.java") and c["kind"] == "code"
                   for c in tracker.citations)
    finally:
        reset_tools()
