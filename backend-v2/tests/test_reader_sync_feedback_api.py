"""Task 3：code/docs 引用预览 + sync events + feedback。reader 用 mini_repo fixture
（复用既有 tests/fixtures/mini_repo 真文件，repos_root 指到 tests/fixtures——与
test_sync_api webhook 同款路径钉法）；sync events 与测前行数对表；feedback 真插入自清理。

三处对 brief 逐字文本的适配（评审授权加固 > 逐字，Task 1 先例）：
1. autouse 测后 ``engine.dispose()`` 清 app 共享 engine 池——``/v1/sync/events`` 与
  feedback 端点经 SessionLocal 用了池化 asyncpg 连接（绑在 TestClient 本测循环上），
  跨测试复用会在 pre-ping 处炸（test_chat_api / test_documents_api 同款处理；旧连接
  关闭发生在另一循环上，sqlalchemy 记 ERROR 日志，临时抬 logger 级别压掉）。
2. ``test_docs_section_not_found`` 里 ``from app.core import doc_search`` 提到
  ``from app.main import app`` 之前（ruff I001；test_sync_api 同款适配）。
3. 评审两处加固：``test_sync_events_empty_contract`` 的 ``total == 0`` 隐含
  「pipeline_events 空表」——本地跑过 smoke_pipe 留账本行就因环境挂；改为与**测前行数**
  对表 + item 键集校验（test_documents_api Task 1 同款），CI 空表时退化为 brief 逐字行为。
  ``test_feedback_roundtrip`` 的 DELETE 原在断言之后同一事务里，断言失败即回滚泄漏
  feedback 行——改为断言在事务外 + DELETE 独立 ``eng.begin()`` 进 finally，无条件清理。
"""
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent import tools_loader
from app.core.config import settings

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _client_env(monkeypatch):
    async def _noop_load(transports=None):
        return None

    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    monkeypatch.setattr("app.api.reader.settings.repos_root", str(FIXTURES))


@pytest.fixture(autouse=True)
def _dispose_app_engine():
    """测后清 app 共享 engine 池（见模块 docstring 适配点）。"""
    import asyncio

    from app.db.base import engine

    yield
    slog = logging.getLogger("sqlalchemy")
    prev, slog.level = slog.level, logging.CRITICAL + 1
    try:
        asyncio.run(engine.dispose())
    finally:
        slog.setLevel(prev)


def test_code_read_window(monkeypatch):
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/v1/code/read", params={
            "repo": "mini_repo", "path": "com/example/broker/CommitLog.java",
            "start_line": 1, "end_line": 3})
        assert r.status_code == 200
        body = r.json()
        assert body["total_lines"] >= 3 and body["start_line"] == 1 and body["end_line"] == 3
        assert isinstance(body["content"], str) and body["content"]


def test_code_read_escape_400_and_missing_404():
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/v1/code/read", params={
            "repo": "mini_repo", "path": "../escape.java"}).status_code == 400
        assert client.get("/v1/code/read", params={
            "repo": "mini_repo", "path": "no/such.java"}).status_code == 404
        assert client.get("/v1/code/read", params={
            "repo": "mini_repo", "path": "a.java", "start_line": 0}).status_code == 422


def test_docs_section_not_found(monkeypatch):
    from app.core import doc_search
    from app.main import app

    monkeypatch.setattr(doc_search, "get_doc_toc", lambda repo: {"toc": []})
    from app.api import reader
    monkeypatch.setattr(reader, "get_doc_toc", doc_search.get_doc_toc)

    with TestClient(app) as client:
        assert client.get("/v1/docs/section", params={
            "repo": "r", "doc_name": "x.md", "anchor": "s-1"}).status_code == 404


def test_sync_events_empty_contract():
    """/v1/sync/events 空表契约——与测前行数对表（评审加固，见模块 docstring 3）。

    CI / 本地空表时 ``before == 0`` 即退化为 brief 逐字断言；本地残留 smoke_pipe
    账本行时按「total == 测前行数」继续成立，不因环境数据挂。
    """
    from sqlalchemy import create_engine, text

    from app.main import app

    eng = create_engine(settings.postgres_dsn_sync)
    try:
        with eng.connect() as conn:
            before = conn.execute(text("select count(*) from pipeline_events")).scalar_one()
    finally:
        eng.dispose()

    with TestClient(app) as client:
        r = client.get("/v1/sync/events")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == before and len(body["items"]) == min(before, 50)
        for item in body["items"]:
            assert set(item) == {"id", "repo", "commit_hash", "path", "event_kind",
                                 "status", "attempts", "last_error",
                                 "created_at", "updated_at"}
        assert client.get("/v1/sync/events", params={"limit": 0}).status_code == 422
        assert client.get("/v1/sync/events", params={"status": "DONE"}).status_code == 200


def test_feedback_roundtrip():
    """无外键设计：不存在的 message_id 也接受；真插入 + 测后自清理（评审加固：无条件）。"""
    from sqlalchemy import create_engine, text

    from app.main import app

    fid = None
    with TestClient(app) as client:
        r = client.post("/v1/chat/messages/999999/feedback",
                        json={"rating": "NOT_HELPFUL", "comment": "行号不对"})
        assert r.status_code == 200 and r.json()["ok"] is True
        fid = r.json()["feedback_id"]
        assert client.post("/v1/chat/messages/1/feedback",
                           json={"rating": "MAYBE"}).status_code == 422
    eng = create_engine(settings.postgres_dsn_sync)
    try:
        with eng.connect() as conn:  # 读校验在事务外——失败不再回滚掉下面的清理
            row = conn.execute(text("select rating, comment from feedback where id = :i"),
                               {"i": fid}).first()
        assert row == ("NOT_HELPFUL", "行号不对")
    finally:
        if fid is not None:  # 独立事务清理：断言失败也把本测插入的行删掉
            with eng.begin() as conn:
                conn.execute(text("delete from feedback where id = :i"), {"i": fid})
        eng.dispose()


def test_feedback_username_off():
    """KEEP②：RBAC off → feedback.username 落 "anonymous"（ANONYMOUS_USER 透传）。"""
    from sqlalchemy import create_engine, text

    from app.core.config import settings
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/v1/chat/messages/999998/feedback",
                        json={"rating": "HELPFUL"})
        assert r.status_code == 200
        fid = r.json()["feedback_id"]
    eng = create_engine(settings.postgres_dsn_sync)
    with eng.begin() as conn:
        u = conn.execute(text("select username from feedback where id=:i"),
                         {"i": fid}).scalar()
        conn.execute(text("delete from feedback where id=:i"), {"i": fid})
    eng.dispose()
    assert u == "anonymous"
