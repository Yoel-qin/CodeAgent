"""检查点老化清理（Phase 7 Milestone 14 Part A）。

LangGraph 的 ``AsyncPostgresSaver`` 把 thread 状态写进 PG 的 ``checkpoints`` /
``checkpoint_writes`` / ``checkpoint_blobs`` 三表（每轮约 18 行，M8 e2e 实测），无保留策略会
无限膨胀。本模块提供**按 thread 整体**的过期清理：仅当一个 thread 的**最新** checkpoint 时间戳
（``checkpoints.checkpoint`` JSONB 的 ``ts`` 键，ISO 8601）早于保留期，才整 thread 删除三表——
活跃/近期 thread 全保留、彻底闲置 thread 整体移除，无「删一半」造成的 resume 歧义。

设计要点：
  - **独立连接**：``AsyncPostgresSaver`` 持单条 autocommit ``AsyncConnection``（非池，受其
    ``asyncio.Lock`` 保护）；为避免与 graph 执行抢锁，清理用**独立** ``psycopg.AsyncConnection``
    （``psycopg[binary]`` 已装，无新依赖）。
  - **裸 SQL**：三表是 langgraph 自有表（``alembic/env.py`` 的 ``_include_object`` 已排除），
    不进 ORM/迁移——同 ``graph_traverse`` 用裸 SQL 之风。
  - **时间戳来源**：三表均无时间戳列；``checkpoints.checkpoint`` JSONB 的 ``ts`` 键是唯一可靠的
    checkpoint 时刻（``checkpoint_id`` 为 UUIDv6 亦可排序，但 ``ts`` 可读且可直接比较）。
"""
from __future__ import annotations

import psycopg
from psycopg import AsyncConnection

from app.core.config import settings

# 三表均以 ``thread_id`` 为键（langgraph 自有，无 FK；alembic env.py 已排除自动管理）。
# 删除顺序无关（无外键约束）；列全量删一个 thread 的所有行。
_CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")

# 命中「整 thread 过期」的 thread_id：最新 checkpoint ts 早于保留期。
# checkpoint JSONB 的 ts 键由 langgraph 写入（datetime.now(utc).isoformat()）。
_STALE_THREADS_SQL = """
    SELECT thread_id FROM checkpoints
    GROUP BY thread_id
    HAVING MAX((checkpoint ->> 'ts')::timestamptz) < now() - make_interval(days => %s)
"""


async def delete_thread_checkpoints(conn: AsyncConnection, thread_id: str) -> int:
    """删除一个 thread 在三表的全部行，返回合计删除行数。

    用于：HITL 中断超时被过期时，立即清掉该 thread 的 checkpoint（让晚到 resume 干净失败，
    见 ``maintenance_service.expire_stale_interrupts``）；亦被 :func:`cleanup_old_checkpoints` 复用。
    """
    total = 0
    for tbl in _CHECKPOINT_TABLES:
        cur = await conn.execute(f"DELETE FROM {tbl} WHERE thread_id = %s", (thread_id,))
        total += cur.rowcount or 0
    return total


async def cleanup_old_checkpoints(conn: AsyncConnection, retention_days: int) -> dict:
    """清理「最新 checkpoint 早于 ``retention_days``」的整个 thread。

    返回 ``{"threads": 删除的 thread 数, "rows": 合计删除行数}``。无命中则不删（返回 0/0）。
    ``retention_days <= 0`` → 直接返回 0/0（禁用清理）。

    不变量：保留期应远大于 HITL 中断超时（如 30d ≫ 24h），故任何待审批 interrupt 早已被
    ``expire_stale_interrupts`` 过期、其 thread checkpoint 已删——此处不会误删仍有待审批的活 thread。
    """
    if retention_days <= 0:
        return {"threads": 0, "rows": 0}
    cur = await conn.execute(_STALE_THREADS_SQL, (retention_days,))
    thread_ids = [r[0] for r in await cur.fetchall()]
    rows = 0
    for tid in thread_ids:
        rows += await delete_thread_checkpoints(conn, tid)
    return {"threads": len(thread_ids), "rows": rows}


async def open_checkpoint_conn() -> AsyncConnection:
    """开一条独立 autocommit 连接供清理用（隔离于 saver 的单连接 + Lock）。

    调用方负责关闭（``async with await open_checkpoint_conn() as conn: ...``）。
    """
    return await psycopg.AsyncConnection.connect(settings.postgres_dsn, autocommit=True)
