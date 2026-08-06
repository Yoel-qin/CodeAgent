"""LangGraph Checkpointer —— 断点续跑 / 多轮状态持久化（Phase 7 Milestone 8）。

两种实现，由 ``settings.langgraph_checkpoint`` 选：

- ``memory``（默认）：进程内 ``MemorySaver``，单 worker dev 够用；**进程重启即丢**全部 LangGraph
  state。零配置、零额外依赖。
- ``postgres``：``AsyncPostgresSaver``（``langgraph.checkpoint.postgres.aio``），thread 状态
  持久化到 PG（``coderag`` 库的 ``checkpoints`` / ``checkpoint_writes`` / ``checkpoint_blobs``
  三表，由 ``setup()`` 自建、幂等）。**跨进程重启存活**，是 resume/中断/HITL/跨轮记忆（M9+）的地基。

生命周期：``init_checkpointer()`` 在 ``main.lifespan`` 启动时建 saver + 建表；``get_graph()``
（惰性单例）首请求编译时经 ``get_checkpointer()`` 取已初始化的 saver；``close_checkpointer()``
在 lifespan 收尾关 saver。非 ``langgraph`` 引擎或非 ``postgres`` 模式 → init/close 均 no-op。

> 用 ``AsyncPostgresSaver.from_conn_string``（而非裸 ``AsyncConnectionPool``）：前者内部配置
> 连接池 autocommit，使 ``setup()`` 的 ``CREATE INDEX CONCURRENTLY``（须事务外）得以执行。
>
> Windows 注意：psycopg async 要求 ``WindowsSelectorEventLoopPolicy``，已在 ``main`` 顶部设置。
>
> 延迟 import（``AsyncPostgresSaver``）放在 ``init_checkpointer`` 内，避免 memory/legacy 模式
> 无谓加载 langgraph-checkpoint-postgres。
"""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from app.core.config import settings

# postgres 模式的 saver / 其上下文管理器：lifespan 启动时由 init_checkpointer 注入，进程级单例。
_pg_saver: BaseCheckpointSaver | None = None
_pg_cm = None  # AsyncPostgresSaver.from_conn_string 返回的异步上下文管理器（__aenter__ 得 saver）


def get_checkpointer() -> BaseCheckpointSaver:
    """返回当前配置的 checkpointer。memory → 新 MemorySaver；postgres → lifespan 注入的 saver。

    postgres 分支若 saver 未初始化（lifespan 未跑/失败）→ RuntimeError（而非静默崩或回退 memory，
    避免持久化被无声丢失）。
    """
    if settings.langgraph_checkpoint == "memory":
        return MemorySaver()
    if settings.langgraph_checkpoint == "postgres":
        if _pg_saver is None:
            raise RuntimeError(
                "postgres 检查点未初始化——main.lifespan 启动时应先 await init_checkpointer()"
            )
        return _pg_saver
    raise NotImplementedError(f"未知 langgraph_checkpoint={settings.langgraph_checkpoint!r}")


async def init_checkpointer() -> None:
    """lifespan 启动调：postgres 模式建 saver（from_conn_string）+ setup() 建表。

    memory 模式 / 非 langgraph 引擎 → no-op。幂等：setup() 用 CREATE TABLE IF NOT EXISTS。
    """
    global _pg_saver, _pg_cm
    # 仅 langgraph + postgres 才需要持久化检查点；legacy/memory 直接跳过（不引入第二连接池）。
    if settings.rag_engine != "langgraph" or settings.langgraph_checkpoint != "postgres":
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    # from_conn_string 内部建 AsyncConnectionPool 并配 autocommit——setup() 的 CREATE INDEX
    # CONCURRENTLY 必须事务外执行（裸 AsyncConnectionPool 默认 autocommit=False 会抛
    # ActiveSqlTransaction）。手动 __aenter__/__aexit__ 让 cm 跨 init→请求→close 存活。
    _pg_cm = AsyncPostgresSaver.from_conn_string(settings.postgres_dsn)
    _pg_saver = await _pg_cm.__aenter__()
    await _pg_saver.setup()  # 幂等建 checkpoints / checkpoint_writes / checkpoint_blobs


async def close_checkpointer() -> None:
    """lifespan 收尾调：退出 saver 的上下文管理器（关其连接池）、清引用。未启用 postgres 时 no-op。"""
    global _pg_saver, _pg_cm
    cm, _pg_saver, _pg_cm = _pg_cm, None, None
    if cm is not None:
        await cm.__aexit__(None, None, None)
