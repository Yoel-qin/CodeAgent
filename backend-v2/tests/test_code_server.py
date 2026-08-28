"""经 stdio 子进程拉起 code-mcp，走真 MCP 协议验证 4 工具。"""
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIX = (Path(__file__).parent / "fixtures").resolve()


def _server_params() -> StdioServerParameters:
    env = {**os.environ, "REPOS_ROOT": str(FIX), "DEFAULT_REPO": "mini_repo"}
    return StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_servers.code_server", "--stdio"], env=env)


@pytest.fixture
async def session():
    cm_stdio = stdio_client(_server_params())
    read, write = await cm_stdio.__aenter__()
    s = ClientSession(read, write)
    await s.__aenter__()
    await s.initialize()
    yield s
    # cleanup — MCP 2.x / anyio task-group teardown raises on Windows + pytest-asyncio
    for _cleanup in (lambda: s.__aexit__(None, None, None), lambda: cm_stdio.__aexit__(None, None, None)):
        try:
            await _cleanup()
        except (RuntimeError, ExceptionGroup, OSError):
            pass


async def _call(session, name, args):
    res = await session.call_tool(name, args)
    return json.loads(res.content[0].text)


async def test_list_tools_has_four(session):
    tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    assert names == {"grep_code", "read_file", "list_directory", "find_symbol"}


async def test_grep_over_mcp(session):
    res = await _call(session, "grep_code", {"pattern": "MAX_RETRY_TIMES"})
    assert any("CommitLog.java" in m["file"] for m in res["matches"])


async def test_read_file_over_mcp(session):
    res = await _call(session, "read_file", {"file_path": "com/example/broker/CommitLog.java", "start_line": 10, "end_line": 10})
    assert "MAX_RETRY_TIMES" in res["content"]


async def test_find_symbol_over_mcp(session):
    res = await _call(session, "find_symbol", {"symbol_name": "putMessage"})
    assert any(loc["kind"] == "method" for loc in res["locations"])


async def test_path_escape_returns_error(session):
    res = await _call(session, "read_file", {"file_path": "../../etc/passwd"})
    assert "error" in res


async def test_max_results_clamped(session):
    res = await _call(session, "grep_code", {"pattern": "e", "max_results": 99999})
    assert len(res["matches"]) <= 100
