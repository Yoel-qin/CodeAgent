import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.chat_service import (
    add_message,
    get_conversation_detail,
    open_conversation,
)


@pytest.fixture
async def async_session():
    # 每测新建 engine（NullPool）：pytest-asyncio 每测换事件循环，
    # 复用共享 engine 的池化连接会撞上一个已关闭的循环（Event loop is closed）。
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    engine = create_async_engine(settings.postgres_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            s = AsyncSession(bind=conn, expire_on_commit=False)
            yield s
            await s.close()
            await tx.rollback()
    finally:
        await engine.dispose()


async def test_open_add_and_detail(async_session):
    conv, cid = await open_conversation(
        async_session, query="sendDefaultImpl 在哪", conversation_id=None, target_repo="rocketmq"
    )
    assert len(cid) == 32
    uid = await add_message(async_session, conv, role="user", content="sendDefaultImpl 在哪")
    aid = await add_message(
        async_session,
        conv,
        role="assistant",
        content="答案",
        meta={"citations": [{"kind": "code"}], "route": "codenav"},
    )
    assert uid != aid
    detail = await get_conversation_detail(async_session, cid)
    assert detail["conversation"]["target_repo"] == "rocketmq"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["meta"]["route"] == "codenav"


async def test_open_existing_conversation(async_session):
    conv1, cid = await open_conversation(
        async_session, query="问题一", conversation_id=None, target_repo="rocketmq"
    )
    conv2, cid2 = await open_conversation(
        async_session, query="问题二", conversation_id=cid, target_repo="rocketmq"
    )
    assert cid2 == cid
