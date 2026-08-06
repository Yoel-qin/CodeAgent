"""检查点老化清理单测（Phase 7 Milestone 14 Part A）。

假 ``_FakeConn``（psycopg 风格：``execute`` 记 stmt、SELECT 走 fetchall / DELETE 走 rowcount），
无需 infra / 真实 PG。
"""
from __future__ import annotations

from app.agent.memory.checkpoint_cleanup import (
    _CHECKPOINT_TABLES,
    cleanup_old_checkpoints,
    delete_thread_checkpoints,
)


class _FakeCur:
    def __init__(self, *, rowcount=0, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []

    async def fetchall(self):
        return self._rows


class _FakeConn:
    """按 SQL 前缀分发：SELECT→fetchall 返 thread_id 列表；DELETE→rowcount。"""

    def __init__(self, *, select_rows=None, delete_rowcount=1):
        self.executes: list[tuple] = []  # [(sql, params), ...]
        self._select_rows = [  # fetchall 返单元素元组（r[0]=thread_id）
            (r,) if isinstance(r, str) else r for r in (select_rows or [])
        ]
        self._delete_rowcount = delete_rowcount

    async def execute(self, sql, params=None):
        self.executes.append((sql, params))
        if sql.strip().upper().startswith("SELECT"):
            return _FakeCur(rows=self._select_rows)
        return _FakeCur(rowcount=self._delete_rowcount)


# ---- delete_thread_checkpoints ----


async def test_delete_thread_checkpoints_deletes_three_tables_and_sums_rowcount():
    conn = _FakeConn(delete_rowcount=2)
    n = await delete_thread_checkpoints(conn, "conv_1")
    assert n == 2 * len(_CHECKPOINT_TABLES)  # 三表各删 rowcount
    assert len(conn.executes) == len(_CHECKPOINT_TABLES)
    for sql, params in conn.executes:
        assert sql.startswith("DELETE FROM checkpoint")
        assert "WHERE thread_id = %s" in sql
        assert params == ("conv_1",)


async def test_delete_thread_checkpoints_zero_rowcount_when_no_rows():
    conn = _FakeConn(delete_rowcount=0)
    assert await delete_thread_checkpoints(conn, "conv_x") == 0
    assert len(conn.executes) == len(_CHECKPOINT_TABLES)  # 仍发了 3 条 DELETE（命中 0 行）


# ---- cleanup_old_checkpoints ----


async def test_cleanup_deletes_each_stale_thread_wholesale():
    conn = _FakeConn(select_rows=["conv_a", "conv_b"], delete_rowcount=3)
    out = await cleanup_old_checkpoints(conn, 30)
    # 1 SELECT + 2 thread × 3 表 DELETE = 7 executes
    assert len(conn.executes) == 1 + 2 * len(_CHECKPOINT_TABLES)
    assert conn.executes[0][0].strip().upper().startswith("SELECT")  # 先选
    assert conn.executes[0][1] == (30,)  # retention_days 参数
    assert out == {"threads": 2, "rows": 2 * 3 * len(_CHECKPOINT_TABLES)}


async def test_cleanup_no_stale_threads_no_deletes():
    conn = _FakeConn(select_rows=[], delete_rowcount=3)
    out = await cleanup_old_checkpoints(conn, 30)
    assert out == {"threads": 0, "rows": 0}
    assert len(conn.executes) == 1  # 仅 SELECT，无 DELETE


async def test_cleanup_retention_le_zero_is_disabled():
    conn = _FakeConn(select_rows=["conv_a"])
    assert await cleanup_old_checkpoints(conn, 0) == {"threads": 0, "rows": 0}
    assert conn.executes == []  # 直接短路，连 SELECT 都不发
