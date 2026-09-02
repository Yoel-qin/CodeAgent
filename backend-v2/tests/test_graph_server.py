"""经 stdio 子进程拉起 graph-mcp，走真 MCP 协议验证 4 工具。"""
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy.orm import Session

from app.pipeline.call_graph import build_call_edges
from app.pipeline.ingest_code import entities_from_parsed, upsert_entities
from app.pipeline.ingest_edges import replace_edges
from app.pipeline.parsing.code_parser import parse_java

FIX = Path(__file__).parent / "fixtures" / "mini_repo"
GRAPH_REPO = "graph_test_tmp2"


@pytest.fixture(scope="module")
def seeded_graph(pg_engine):
    """mini fixture 实体+边入 PG（独立 repo 名，用后清理）。"""
    pfs = [parse_java(p.read_text(encoding="utf-8"), str(p.relative_to(FIX)))
           for p in sorted(FIX.rglob("*.java"))]
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(f"DELETE FROM code_entities WHERE repo = '{GRAPH_REPO}'")
    with Session(pg_engine) as s:
        for pf in pfs:
            upsert_entities(s, entities_from_parsed(pf, repo=GRAPH_REPO, module="com"))
        replace_edges(s, repo=GRAPH_REPO, edges=build_call_edges(pfs))
        s.commit()
    yield
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(f"DELETE FROM code_entities WHERE repo = '{GRAPH_REPO}'")


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_servers.graph_server", "--stdio"],
    )


@pytest.fixture
async def session(seeded_graph):
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


async def _call(s, name, args):
    res = await s.call_tool(name, args)
    return json.loads(res.content[0].text)


async def test_graph_tools_list_and_call(session):
    tools = await session.list_tools()
    assert {t.name for t in tools.tools} == {"get_callees", "get_callers", "get_module_deps", "code_metrics"}
    res = json.loads((await session.call_tool("get_callees", {
        "repo": "graph_test_tmp2", "class_name": "CommitLog", "method": "putMessage"})).content[0].text)
    assert any(e["callee_class"] == "FlushService" for e in res.get("edges", []))
