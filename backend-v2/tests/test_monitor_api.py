"""Task 10：monitor API。overview/traces/pipeline 三面——服务层 seed 断言指标、
API 层空库契约 + Redis 段软失败（pipeline 端点 Redis 挂不 500）。

两处对 brief 逐字文本的加固（沿 test_reader_sync_feedback_api / test_documents_api
的「与常驻数据对表」先例精神；均在**回滚事务内**改写可见性，不真删库数据）：
1. ``seeded_trace`` 在 seed 前先于同一回滚事务内清空 ``trace_spans`` 常驻行并防撞
   ``conversations.id='c1'``——overview/list 的 ``window=all`` 聚合无任何过滤条件，
   任何残留行（中途崩溃的 chat 测试进程遗留）都会污染 requests/均值/命中率断言；
   rollback 后原样恢复，CI 空表时该语句零影响（退化为 brief 逐字行为）。
2. API 两测钉 ``app.api.monitor.SessionLocal`` 为隔离工厂（``_isolated_sessions``）：
   每次端点调用新建 NullPool 连接 + 连接级事务，先清空 trace_spans 再放行——
   API 层「空库契约」（requests==0 / total==0 / hit_rate None / 404）由此真正确定；
   连接在端点自己的事件循环（TestClient portal 线程）里建立，绝不跨循环复用
   fixture 连接（test_chat_api 先例：池化连接绑旧循环跨测试复用会在 pre-ping 处炸）。
   monitor 端点因此全程不触 app 共享 engine 连接池，无需 dispose 兜底 fixture。
"""
import json

import pytest
from sqlalchemy import text


@pytest.fixture
async def seeded_trace():
    """会话姿势逐字沿 test_chat_service.async_session（NullPool + 连接级事务 + rollback）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    engine = create_async_engine(settings.postgres_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            session = AsyncSession(bind=conn, expire_on_commit=False)
            # 加固①：同事务内清掉常驻 trace 行 + 防撞会话 id（rollback 恢复，见模块 docstring）
            await session.execute(text("delete from trace_spans"))
            await session.execute(text("delete from conversations where id = 'c1'"))
            await session.execute(text(
                "insert into conversations (id, target_repo, title) values ('c1', 'r', 't')"))
            cid = "c1"
            # (route, tool_span 数, duration, citations)
            for i, (route, n_tools, dur, citations) in enumerate([
                ("codenav", 2, 1000, [{"kind": "code"}]),
                ("codenav", 1, 3000, [{"kind": "doc"}]),
                ("docqa", 0, 2000, []),
            ]):
                m = await session.execute(text(
                    "insert into chat_messages (conversation_id, role, content, meta)"
                    " values (:c, 'assistant', 'a', cast(:meta as jsonb)) returning id"),
                    {"c": cid, "meta": json.dumps({"citations": citations})})
                mid = m.first()[0]
                await session.execute(text(
                    "insert into trace_spans (message_id, conversation_id, query, route,"
                    " spans, duration_ms, token_usage) values (:m, :c, :q, :r,"
                    " cast(:s as jsonb), :d, cast(:t as jsonb))"),
                    {"m": mid, "c": cid, "q": f"q{i}", "r": route,
                     "s": json.dumps([{"kind": "tool"}] * n_tools), "d": dur,
                     "t": json.dumps({"spent_tokens": 100, "llm_calls": 2, "estimated": False})})
            yield session
            await session.close()
            await tx.rollback()
    finally:
        await engine.dispose()


async def test_overview_metrics(seeded_trace):
    from app.services.monitor_service import overview
    o = await overview(seeded_trace, window="all")
    assert o["requests"] == 3 and o["routes"] == {"codenav": 2, "docqa": 1}
    assert o["avg_tool_calls"] == 1.0  # (2+1+0)/3
    assert o["avg_tokens"] == 100.0
    assert o["codenav_hit_rate"] == 0.5  # 2 条 codenav，1 条有 code 引用
    assert o["p50_ms"] is not None and o["avg_duration_ms"] == 2000.0


async def test_list_and_get_trace(seeded_trace):
    from app.services.monitor_service import get_trace, list_traces
    lst = await list_traces(seeded_trace, window="all", limit=10)
    assert lst["total"] == 3 and len(lst["items"]) == 3
    item = lst["items"][0]
    assert item["n_tool_calls"] == 0  # id 倒序最新 = docqa 零工具
    detail = await get_trace(seeded_trace, item["message_id"])
    assert detail["legacy"] is False and detail["summary"]["n_spans"] == 0
    assert await get_trace(seeded_trace, 999999) is None


async def test_overview_window_scopes_sample_segment(seeded_trace):
    """评审修复轮 1①：overview Python 样本段与 SQL 段同窗——早于「today」截断的
    行不得进 routes/codenav_hit_rate/avg_tokens（all 窗仍全量可见）。"""
    from datetime import UTC, datetime, timedelta

    import pytest as _pytest

    from app.services.monitor_service import overview

    old_ts = datetime.now(UTC) - timedelta(days=2)  # 窗外于 today、窗内于 7d/all
    m = await seeded_trace.execute(text(
        "insert into chat_messages (conversation_id, role, content, meta)"
        " values ('c1', 'assistant', 'a', cast(:meta as jsonb)) returning id"),
        {"meta": json.dumps({"citations": [{"kind": "code"}]})})
    old_mid = m.first()[0]
    await seeded_trace.execute(text(
        "insert into trace_spans (message_id, conversation_id, query, route, spans,"
        " duration_ms, token_usage, created_at)"
        " values (:m, 'c1', 'q_old', 'codenav', '[]'::jsonb, 500,"
        " cast(:t as jsonb), cast(:ts as timestamptz))"),
        {"m": old_mid, "t": json.dumps({"spent_tokens": 999, "llm_calls": 9}), "ts": old_ts})

    all_win = await overview(seeded_trace, window="all")
    assert all_win["requests"] == 4 and all_win["routes"]["codenav"] == 3
    assert all_win["codenav_hit_rate"] == _pytest.approx(2 / 3)  # 3 codenav 中 2 条 code 引用

    today = await overview(seeded_trace, window="today")
    assert today["requests"] == 3
    assert today["routes"] == {"codenav": 2, "docqa": 1}  # 旧 codenav 行不进样本段
    assert today["codenav_hit_rate"] == 0.5
    assert today["avg_tokens"] == 100.0  # 旧行 spent_tokens=999 不进均值
    assert today["avg_tool_calls"] == 1.0


def test_api_contracts(monkeypatch):
    from fastapi.testclient import TestClient

    from app.agent import tools_loader
    from app.api import monitor as mon
    from app.main import app

    async def _noop_load(transports=None):
        return None

    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    monkeypatch.setattr(mon, "SessionLocal", _isolated_sessions())  # 加固②：空库契约确定性
    with TestClient(app) as client:
        o = client.get("/v1/monitor/overview", params={"window": "all"}).json()
        assert o["requests"] == 0 and o["codenav_hit_rate"] is None
        t = client.get("/v1/monitor/traces", params={"window": "all"}).json()
        assert t["total"] == 0 and t["items"] == []
        assert client.get("/v1/monitor/traces/999999").status_code == 404
        p = client.get("/v1/monitor/pipeline").json()
        assert "stream" in p and "dead" in p and "events" in p  # Redis 段可 null 但键在


def test_api_pipeline_redis_down(monkeypatch):
    from app.api import monitor as mon
    from app.services import monitor_service as svc

    def _boom(*a, **kw):
        raise RuntimeError("redis down")

    monkeypatch.setattr(svc, "_redis_stream_stats", _boom)
    from fastapi.testclient import TestClient

    from app.agent import tools_loader
    from app.main import app

    async def _noop_load(transports=None):
        return None

    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    monkeypatch.setattr(mon, "SessionLocal", _isolated_sessions())  # 加固②：不触共享 engine 池
    with TestClient(app) as client:
        p = client.get("/v1/monitor/pipeline")
        assert p.status_code == 200 and p.json()["stream"] is None


def _isolated_sessions():
    """替代 ``app.api.monitor.SessionLocal`` 的工厂（加固②，见模块 docstring）：
    每次调用 = 新 NullPool 连接 + 连接级事务，事务内清空 trace_spans，随事务回滚恢复。"""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    class _Ctx:
        async def __aenter__(self):
            self._engine = create_async_engine(settings.postgres_dsn, poolclass=NullPool)
            self._conn = await self._engine.connect()
            self._tx = await self._conn.begin()
            await self._conn.execute(text("delete from trace_spans"))
            self._session = AsyncSession(bind=self._conn, expire_on_commit=False)
            return self._session

        async def __aexit__(self, *exc):
            await self._tx.rollback()
            await self._conn.close()
            await self._engine.dispose()
            return False

    return _Ctx
