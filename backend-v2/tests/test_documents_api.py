"""Task 1：repos/documents 读 API。服务层真 SQL（回滚不留痕）；API 层空库契约。

两处测试环境适配（断言语义不动）：
1. autouse 测后 ``engine.dispose()`` 清 app 共享 engine 池——TestClient 每个上下文起
   独立事件循环，documents 端点经 SessionLocal 用了池化 asyncpg 连接（绑在本测循环上），
   跨测试复用会在 pre-ping 处炸（test_chat_api 同款处理；旧连接关闭发生在另一循环上，
   sqlalchemy 记 ERROR 日志，临时抬 logger 级别压掉）。
2. ``total == 0 and items == []`` 隐含「documents 空表」——CI（零数据）成立，但本地
   coderag_v2 常驻 Plan 2 的 sa-token 12 篇锚点文档（真实数据，不可删）。沿
   test_chat_api 先例（「不得假设库里只有本测的行」）改为与**测前行数**对表 + 逐项
   校验 item 键集：CI 上行数为 0 时即退化为 brief 逐字行为，空/非空两态下 API 契约
   （200 / total 与 items 一致 / 键集）不变。
"""
import logging

import pytest

from app.db.models.doc import DocSection, Document

_LIST_ITEM_KEYS = {"id", "repo", "doc_name", "module", "doc_type", "status",
                   "section_count", "created_at"}


@pytest.fixture(autouse=True)
def _dispose_app_engine():
    """测后清 app 共享 engine 池（本文件唯一动共享 engine 的是 API 层测试）。"""
    import asyncio

    from app.db.base import engine

    yield
    slog = logging.getLogger("sqlalchemy")
    prev, slog.level = slog.level, logging.CRITICAL + 1
    try:
        asyncio.run(engine.dispose())
    finally:
        slog.setLevel(prev)


def _documents_count() -> int:
    """documents 现存行数（独立 sync engine：不与 app 的 async engine 共享连接）。"""
    from sqlalchemy import create_engine, text

    from app.core.config import settings

    engine = create_engine(settings.postgres_dsn_sync)
    try:
        with engine.connect() as conn:
            return conn.execute(text("select count(*) from documents")).scalar_one()
    finally:
        engine.dispose()


@pytest.fixture
async def seeded_docs():
    """两文档三节。会话姿势逐字沿 test_chat_service.async_session：每测 NullPool
    engine + 连接级事务，teardown rollback——真 SQL 真回滚不留痕（直接
    ``async with SessionLocal()`` + begin 不能用：块正常退出即 commit 会泄漏 seed）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    engine = create_async_engine(settings.postgres_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            session = AsyncSession(bind=conn, expire_on_commit=False)
            d1 = Document(repo="r1", doc_name="a.md", module="m", source_path="a.md",
                          doc_type="markdown", status="COMPLETED", file_hash="h1")
            d2 = Document(repo="r2", doc_name="b.pdf", module=None, source_path="b.pdf",
                          doc_type="pdf", status="PARTIAL", file_hash="h2")
            session.add_all([d1, d2])
            await session.flush()
            session.add_all([
                DocSection(document_id=d1.id, repo="r1", anchor="s-1", title="一", level=1,
                           kind="text", content="c1", token_count=2, order_index=0),
                DocSection(document_id=d1.id, repo="r1", anchor="s-2", title="二", level=2,
                           kind="table", content="c2", token_count=2, order_index=1),
                DocSection(document_id=d2.id, repo="r2", anchor="s-1", title="x", level=1,
                           kind="text", content="c3", token_count=1, order_index=0),
            ])
            await session.flush()
            yield session, d1.id, d2.id
            await session.close()
            await tx.rollback()
    finally:
        await engine.dispose()


async def test_list_documents_repo_filter_and_counts(seeded_docs):
    from app.services.document_service import list_documents
    session, d1_id, d2_id = seeded_docs
    total, rows = await list_documents(session, repo="r1", limit=10, offset=0)
    assert total == 1 and rows[0][0].id == d1_id and rows[0][1] == 2  # (Document, section_count)


async def test_get_sections_ordered(seeded_docs):
    from app.services.document_service import get_document_with_sections
    session, d1_id, _ = seeded_docs
    detail = await get_document_with_sections(session, d1_id)
    assert [s["anchor"] for s in detail["sections"]] == ["s-1", "s-2"]
    assert detail["sections"][1]["kind"] == "table"


async def test_get_sections_missing_returns_none(seeded_docs):
    from app.services.document_service import get_document_with_sections
    session, _, _ = seeded_docs
    assert await get_document_with_sections(session, 999999) is None


def test_api_empty_contracts(monkeypatch, tmp_path):
    """API 契约：repos 返回目录名列表；documents 200 + total/items 一致；sections
    404；limit/offset 越界 422。空/非空两态皆成立（见模块 docstring 第 2 点）。"""
    from fastapi.testclient import TestClient

    from app.agent import tools_loader
    from app.main import app

    async def _noop_load(transports=None):
        return None

    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    (tmp_path / "zeta").mkdir()
    (tmp_path / "alpha").mkdir()
    monkeypatch.setattr("app.api.repos.settings.repos_root", str(tmp_path))
    before = _documents_count()
    with TestClient(app) as client:
        assert client.get("/v1/repos").json() == {"items": ["alpha", "zeta"]}
        r = client.get("/v1/documents")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == before and len(body["items"]) == before
        assert all(set(item) == _LIST_ITEM_KEYS for item in body["items"])
        assert client.get("/v1/documents/999999/sections").status_code == 404
        assert client.get("/v1/documents", params={"limit": 0}).status_code == 422
        assert client.get("/v1/documents", params={"limit": 201}).status_code == 422
        assert client.get("/v1/documents", params={"offset": -1}).status_code == 422
