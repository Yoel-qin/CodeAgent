"""经 stdio 子进程拉起 doc-mcp，走真 MCP 协议验证 5 工具。"""
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIX = (Path(__file__).parent / "fixtures").resolve()


def _server_params() -> StdioServerParameters:
    env = {
        **os.environ,
        "REPOS_ROOT": str(FIX),
        "DEFAULT_REPO": "mini_repo",
        "EMBEDDING_API_KEY": "",
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_servers.doc_server", "--stdio"],
        env=env,
    )


@pytest.fixture
async def session():
    cm_stdio = stdio_client(_server_params())
    read, write = await cm_stdio.__aenter__()
    s = ClientSession(read, write)
    await s.__aenter__()
    await s.initialize()
    yield s
    # cleanup — MCP 2.x / anyio task-group teardown raises on Windows + pytest-asyncio
    for _cleanup in (
        lambda: s.__aexit__(None, None, None),
        lambda: cm_stdio.__aexit__(None, None, None),
    ):
        try:
            await _cleanup()
        except (RuntimeError, ExceptionGroup, OSError):
            pass


async def _call(session, name, args):
    res = await session.call_tool(name, args)
    return json.loads(res.content[0].text)


async def test_list_tools_has_five(session):
    tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    assert names == {
        "doc_semantic_search",
        "doc_keyword_search",
        "doc_hybrid_search",
        "read_doc_section",
        "get_doc_toc",
    }


async def test_doc_semantic_search_empty_key(session):
    """EMBEDDING_API_KEY="" 时 semantic_search 返回空形。"""
    res = await _call(session, "doc_semantic_search", {"query": "x", "repo": "nonexist"})
    assert res == {"results": [], "recall": 0}


async def test_doc_hybrid_search_top_k_clamp(session):
    """top_k=999 不炸，server 不崩。"""
    res = await _call(
        session, "doc_hybrid_search", {"query": "x", "top_k": 999, "repo": "nonexist"}
    )
    # ES 可能不可用（8s 超时），只断言不炸（有 results 或 error 均可）
    assert "results" in res or "error" in res
