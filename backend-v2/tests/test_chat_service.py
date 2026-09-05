import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.chat_service import (
    add_message,
    get_conversation_detail,
    list_conversations,
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


async def test_list_conversations_orders_by_recent_activity(async_session):
    conv_a, cid_a = await open_conversation(
        async_session, query="问题A", conversation_id=None, target_repo="rocketmq"
    )
    await asyncio.sleep(0.01)  # 隔开时钟粒度，保证 B.updated_at 严格小于 A 的新活跃时间
    _conv_b, cid_b = await open_conversation(
        async_session, query="问题B", conversation_id=None, target_repo="rocketmq"
    )
    assert cid_a != cid_b
    # 老会话 A 补发消息 → updated_at 反超 B → 列表首位应是 A（最近活跃在前）
    await add_message(async_session, conv_a, role="user", content="追问")
    # 全表过滤到本测创建的两条：list_conversations 是全表查询，不得假设库里只有本测的行
    rows = [c for c in await list_conversations(async_session) if c.id in (cid_a, cid_b)]
    assert [c.id for c in rows] == [cid_a, cid_b]
    assert rows[0].updated_at > rows[0].created_at


# ── KEEP②：feedback 用户归属 ────────────────────────────────────────────────


async def test_add_feedback_username(async_session):
    """username 参数落列；缺省 None（历史/直调兼容）。

    brief 逐字用 conftest 的 ``session``（同步 Session）——异步 service 需
    AsyncSession，故换本文件既有的 ``async_session``（同款连接级事务回滚不留痕）；
    ``session.get`` 相应加 await。第二处适配：brief 签名里 ``comment`` 是必填
    keyword-only（Step 3c 逐字），第二通调用补 ``comment=None``。
    """
    from app.db.models import Feedback
    from app.services import chat_service

    fid = await chat_service.add_feedback(async_session, 1, rating="HELPFUL",
                                          comment=None, username="alice")
    fb = await async_session.get(Feedback, fid)
    assert fb is not None and fb.username == "alice"

    fid2 = await chat_service.add_feedback(async_session, 2, rating="NOT_HELPFUL",
                                           comment=None)
    assert (await async_session.get(Feedback, fid2)).username is None
