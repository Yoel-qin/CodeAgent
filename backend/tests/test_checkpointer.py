"""checkpointer 单测（Phase 7 Milestone 8）——纯/mock，无 PG/网络。

覆盖：``postgres_dsn`` 格式、``get_checkpointer()`` 三分支、``init/close`` 生命周期
（fake ``from_conn_string`` 上下文管理器 + saver，断言 setup/__aexit__ 调用与 no-op 分支）。
"""
from __future__ import annotations

import pytest

from app.agent.memory import checkpointer as ckpt
from app.core.config import settings

# ---- postgres_dsn 格式 ----


def test_postgres_dsn_is_raw_psycopg_scheme():
    dsn = settings.postgres_dsn
    assert dsn.startswith("postgresql://")
    # 不得带 SQLAlchemy 驱动前缀（langgraph 检查点 / psycopg_pool 用裸 postgresql://）
    assert "+psycopg" not in dsn
    assert "+asyncpg" not in dsn
    assert settings.postgres_db in dsn
    assert f":{settings.postgres_port}/" in dsn


# ---- get_checkpointer 分支 ----


def test_get_checkpointer_memory(monkeypatch):
    monkeypatch.setattr(settings, "langgraph_checkpoint", "memory")
    from langgraph.checkpoint.memory import MemorySaver

    saver = ckpt.get_checkpointer()
    assert isinstance(saver, MemorySaver)


def test_get_checkpointer_postgres_uninitialized_raises(monkeypatch):
    monkeypatch.setattr(settings, "langgraph_checkpoint", "postgres")
    monkeypatch.setattr(ckpt, "_pg_saver", None)
    with pytest.raises(RuntimeError):
        ckpt.get_checkpointer()


def test_get_checkpointer_postgres_returns_injected_saver(monkeypatch):
    monkeypatch.setattr(settings, "langgraph_checkpoint", "postgres")
    fake = object()
    monkeypatch.setattr(ckpt, "_pg_saver", fake)
    assert ckpt.get_checkpointer() is fake


def test_get_checkpointer_unknown_raises(monkeypatch):
    monkeypatch.setattr(settings, "langgraph_checkpoint", "redis")
    with pytest.raises(NotImplementedError):
        ckpt.get_checkpointer()


# ---- init / close ----


async def test_init_noop_when_not_langgraph(monkeypatch):
    monkeypatch.setattr(settings, "rag_engine", "legacy")
    monkeypatch.setattr(settings, "langgraph_checkpoint", "postgres")
    monkeypatch.setattr(ckpt, "_pg_saver", None)
    monkeypatch.setattr(ckpt, "_pg_cm", None)
    await ckpt.init_checkpointer()
    assert ckpt._pg_saver is None
    assert ckpt._pg_cm is None


async def test_init_noop_when_memory(monkeypatch):
    monkeypatch.setattr(settings, "rag_engine", "langgraph")
    monkeypatch.setattr(settings, "langgraph_checkpoint", "memory")
    monkeypatch.setattr(ckpt, "_pg_saver", None)
    monkeypatch.setattr(ckpt, "_pg_cm", None)
    await ckpt.init_checkpointer()
    assert ckpt._pg_saver is None
    assert ckpt._pg_cm is None


async def test_close_noop_when_uninitialized(monkeypatch):
    monkeypatch.setattr(ckpt, "_pg_saver", None)
    monkeypatch.setattr(ckpt, "_pg_cm", None)
    await ckpt.close_checkpointer()  # 不应抛
    assert ckpt._pg_saver is None
    assert ckpt._pg_cm is None


async def test_init_close_lifecycle(monkeypatch):
    """postgres 模式：from_conn_string 上下文管理器经 __aenter__ 取 saver、setup() 建表；
    close 经 __aexit__ 关闭、清引用。

    init_checkpointer 内部用 ``AsyncPostgresSaver.from_conn_string``（运行时查找类属性），
    故 patch 类的 from_conn_string 即可注入 fake cm。
    """
    import langgraph.checkpoint.postgres.aio as pg_aio

    monkeypatch.setattr(settings, "rag_engine", "langgraph")
    monkeypatch.setattr(settings, "langgraph_checkpoint", "postgres")
    monkeypatch.setattr(ckpt, "_pg_saver", None)
    monkeypatch.setattr(ckpt, "_pg_cm", None)

    calls: dict = {}

    class FakeSaver:
        def __init__(self):
            self.setup_called = False

        async def setup(self):
            self.setup_called = True

    class FakeCM:
        """from_conn_string 返回的异步上下文管理器：__aenter__ 得 saver、__aexit__ 关闭。"""

        def __init__(self, dsn):
            calls["dsn"] = dsn
            self.saver = FakeSaver()
            self.exited = False

        async def __aenter__(self):
            return self.saver

        async def __aexit__(self, *exc):
            self.exited = True

    def fake_from_conn_string(dsn):
        cm = FakeCM(dsn)
        calls["cm"] = cm
        return cm

    monkeypatch.setattr(
        pg_aio.AsyncPostgresSaver, "from_conn_string", staticmethod(fake_from_conn_string)
    )

    await ckpt.init_checkpointer()

    # saver 经 cm.__aenter__ 注入、setup() 调一次、DSN 透传、cm 缓存
    assert ckpt._pg_saver is calls["cm"].saver
    assert ckpt._pg_saver.setup_called is True
    assert ckpt._pg_cm is calls["cm"]
    assert calls["dsn"] == settings.postgres_dsn

    # close：cm.__aexit__ 被调、清模块引用
    cm_ref = ckpt._pg_cm
    await ckpt.close_checkpointer()
    assert cm_ref.exited is True
    assert ckpt._pg_saver is None
    assert ckpt._pg_cm is None
