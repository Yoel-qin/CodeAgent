"""Task 3：code/docs 引用预览 + sync events + feedback。reader 用 mini_repo fixture
（复用既有 tests/fixtures/mini_repo 真文件，repos_root 指到 tests/fixtures——与
test_sync_api webhook 同款路径钉法）；sync events 空表契约；feedback 真插入自清理。

两处测试环境适配（brief 断言逐行不动）：
1. autouse 测后 ``engine.dispose()`` 清 app 共享 engine 池——``/v1/sync/events`` 与
  feedback 端点经 SessionLocal 用了池化 asyncpg 连接（绑在 TestClient 本测循环上），
  跨测试复用会在 pre-ping 处炸（test_chat_api / test_documents_api 同款处理；旧连接
  关闭发生在另一循环上，sqlalchemy 记 ERROR 日志，临时抬 logger 级别压掉）。
2. ``test_docs_section_not_found`` 里 ``from app.core import doc_search`` 提到
  ``from app.main import app`` 之前（ruff I001；test_sync_api 同款适配）。
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
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/v1/sync/events")
        assert r.status_code == 200 and r.json()["total"] == 0 and r.json()["items"] == []
        assert client.get("/v1/sync/events", params={"limit": 0}).status_code == 422
        assert client.get("/v1/sync/events", params={"status": "DONE"}).status_code == 200


def test_feedback_roundtrip():
    """无外键设计：不存在的 message_id 也接受；真插入 + 测后自清理。"""
    from sqlalchemy import create_engine, text

    from app.main import app

    with TestClient(app) as client:
        r = client.post("/v1/chat/messages/999999/feedback",
                        json={"rating": "NOT_HELPFUL", "comment": "行号不对"})
        assert r.status_code == 200 and r.json()["ok"] is True
        fid = r.json()["feedback_id"]
        assert client.post("/v1/chat/messages/1/feedback",
                           json={"rating": "MAYBE"}).status_code == 422
    eng = create_engine(settings.postgres_dsn_sync)
    try:
        with eng.begin() as conn:
            row = conn.execute(text("select rating, comment from feedback where id = :i"),
                               {"i": fid}).first()
            assert row == ("NOT_HELPFUL", "行号不对")
            conn.execute(text("delete from feedback where id = :i"), {"i": fid})
    finally:
        eng.dispose()
