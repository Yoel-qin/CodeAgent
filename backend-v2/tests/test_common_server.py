"""经 stdio 子进程拉起 common-mcp，走真 MCP 协议验证 3 轻量工具。

submit_feedback 直写真 PG（stdio 子进程独立连接，无法靠事务回滚），
故用标记 comment「测试-v3」+ 前后 DELETE 清理。
"""
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import create_engine, text

from app.core.config import settings

FIX = (Path(__file__).parent / "fixtures").resolve()  # noqa: F841 —— 沿 test_code_server 形状（本 server 无仓库入参）
MARK = "测试-v3"


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", "app.mcp_servers.common_server", "--stdio"], env=dict(os.environ)
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
    for _cleanup in (lambda: s.__aexit__(None, None, None), lambda: cm_stdio.__aexit__(None, None, None)):
        try:
            await _cleanup()
        except (RuntimeError, ExceptionGroup, OSError):
            pass


@pytest.fixture(autouse=True)
def _clean_feedback_rows():
    """每条测试前后清掉本测试的标记行（子进程独立连接写入，只能真删）。"""
    _delete_marked_rows()
    yield
    _delete_marked_rows()


def _delete_marked_rows() -> None:
    eng = create_engine(settings.postgres_dsn_sync)
    try:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM feedback WHERE comment = :m"), {"m": MARK})
    finally:
        eng.dispose()


def _count_marked_rows() -> int:
    eng = create_engine(settings.postgres_dsn_sync)
    try:
        with eng.connect() as conn:
            return int(conn.execute(text("SELECT count(*) FROM feedback WHERE comment = :m"), {"m": MARK}).scalar())
    finally:
        eng.dispose()


async def _call(session, name, args):
    res = await session.call_tool(name, args)
    return json.loads(res.content[0].text)


async def test_list_tools_has_three(session):
    tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    assert names == {"get_current_time", "clarify_question", "submit_feedback"}


async def test_get_current_time_over_mcp(session):
    res = await _call(session, "get_current_time", {})
    assert "iso" in res and "tz" in res
    assert "T" in res["iso"]


async def test_clarify_question_over_mcp(session):
    res = await _call(session, "clarify_question", {"text": "RocketMQ 消息堆积怎么排查"})
    assert res["question"].startswith("关于「RocketMQ 消息堆积怎么排查")
    assert "类名" in res["question"]


async def test_clarify_question_truncates_to_80(session):
    res = await _call(session, "clarify_question", {"text": "长" * 200})
    assert res["question"].startswith("关于「" + "长" * 80)


async def test_submit_feedback_writes_pg(session):
    res = await _call(session, "submit_feedback", {"rating": "HELPFUL", "comment": MARK, "message_id": None})
    assert res.get("ok") is True
    assert isinstance(res["id"], int)
    assert _count_marked_rows() == 1


async def test_submit_feedback_invalid_rating(session):
    res = await _call(session, "submit_feedback", {"rating": "XX", "comment": MARK})
    assert "error" in res
    assert _count_marked_rows() == 0  # 校验失败不落行
