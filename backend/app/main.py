"""CodeRAG FastAPI 入口。"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import __version__
from app.agent.memory.checkpointer import close_checkpointer, init_checkpointer
from app.agent.tools.web_tools import init_web_tools
from app.api.v1.router import api_router
from app.clients.mcp_client import close_mcp_client, init_mcp_client
from app.core.config import settings
from app.core.logging import setup_logging

# Windows + psycopg async（langgraph AsyncPostgresSaver）需 SelectorEventLoop：默认的
# ProactorEventLoop 与其不兼容（"cannot use the ProactorEventLoop to run in async mode"）。
# asyncpg / httpx / redis / milvus 在 Selector 下均正常。
#
# 关键坑：uvicorn 0.51 在 win32 上**硬编码** ProactorEventLoop——其 Server.run() 用
# asyncio.run(serve, loop_factory=config.get_loop_factory())，而 get_loop_factory 直接返回
# asyncio.ProactorEventLoop，**绕过** asyncio 事件循环策略。故仅 set_event_loop_policy 无效。
# 解决：仅当启用 postgres 检查点时，覆盖 uvicorn 的 loop factory 为 Selector（import app.main
# 早于 server.run() 取 factory，故覆盖生效）。默认 legacy/memory 路径完全不触发，零影响。
# Linux 生产无需此处理（uvicorn 默认即 epoll/Selector 兼容）。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if settings.rag_engine == "langgraph" and settings.langgraph_checkpoint == "postgres":
        import uvicorn.config

        def _selector_loop_factory(self):  # noqa: ANN001  返回 SelectorEventLoop 类，绕过 Proactor
            return asyncio.SelectorEventLoop

        uvicorn.config.Config.get_loop_factory = _selector_loop_factory  # type: ignore[method-assign]


def _run_resync_once(engine) -> None:
    """单次 embedding_synced 补偿（同步；由后台循环跑在独立线程里）。

    延迟 import 以保持启动路径精简，且仅在 ``ingest_resync_enabled`` 时加载。
    """
    from app.pipeline.indexing import resync_pending_embeddings

    with Session(engine) as session:
        try:
            res = resync_pending_embeddings(
                session,
                strategy=settings.embedding_strategy,
                limit=settings.ingest_resync_batch_limit,
            )
            logger.info(f"[resync] embedding_synced 补偿完成 {res}")
        except Exception as e:  # 整轮失败不影响下一轮调度
            logger.error(f"[resync] 补偿失败 {type(e).__name__}: {e}")


async def _run_maintenance_once() -> None:
    """单次运营维护（异步，M14）：HITL 中断超时过期 + 检查点老化清理。

    ① 过期超时未 resume 的 ``interrupted`` 消息 → ``status='expired'``（chat_messages 真相源，
      与检查点模式无关）；② 仅 postgres 检查点：清刚过期 thread 的 checkpoint + 整 thread 老化清理
      （memory 模式无 PG checkpoint 行，跳过；其 MemorySaver 态随进程重启自然消失）。
    """
    from app.agent.memory.checkpoint_cleanup import (
        cleanup_old_checkpoints,
        delete_thread_checkpoints,
        open_checkpoint_conn,
    )
    from app.db import AsyncSessionLocal
    from app.services.maintenance_service import expire_stale_interrupts

    expired: list[str] = []
    if settings.hitl_interrupt_timeout_hours > 0:
        async with AsyncSessionLocal() as session:
            try:
                expired = await expire_stale_interrupts(session, settings.hitl_interrupt_timeout_hours)
                if expired:
                    logger.info(f"[maintenance] 过期中断 {len(expired)} 条 → status=expired")
            except Exception as e:  # 单步失败不影响后续步骤
                logger.error(f"[maintenance] 中断过期失败 {type(e).__name__}: {e}")

    # 仅 postgres 模式触碰 checkpoint 三表（memory 模式无此表，delete 会报错）
    if settings.langgraph_checkpoint == "postgres" and (
        expired or settings.checkpoint_retention_days > 0
    ):
        try:
            async with await open_checkpoint_conn() as conn:
                for tid in expired:  # 刚过期 thread 立即清 checkpoint，让晚到 resume 干净失败
                    await delete_thread_checkpoints(conn, tid)
                if settings.checkpoint_retention_days > 0:
                    res = await cleanup_old_checkpoints(conn, settings.checkpoint_retention_days)
                    logger.info(f"[maintenance] 检查点老化清理完成 {res}")
        except Exception as e:
            logger.error(f"[maintenance] 检查点清理失败 {type(e).__name__}: {e}")


async def _run_staleness_sweep_once() -> None:
    """单次主动腐化巡检（异步，M16）：全库枚举非过时 DOC↔CODE 关系 → change_history 启发式判定 → 标 is_stale。

    纯 PG，不依赖 langgraph；service 内部永不抛（异常落 error dict），本层仅 log。
    """
    from app.db import AsyncSessionLocal
    from app.services.staleness_sweep_service import run_staleness_sweep

    try:
        async with AsyncSessionLocal() as session:
            res = await run_staleness_sweep(session, batch_size=settings.staleness_sweep_batch_size)
        if res.get("error"):
            logger.error(f"[staleness-sweep] 巡检失败 {res['error']}")
        elif res["marked"]:
            logger.info(f"[staleness-sweep] 巡检完成 {res}")
    except Exception as e:  # noqa: BLE001  整轮失败不杀循环
        logger.error(f"[staleness-sweep] 巡检失败 {type(e).__name__}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    resync_task = None
    engine = None
    if settings.ingest_resync_enabled:
        # 自建同步 engine（镜像脚本约定，不往 db/__init__ 加 sync factory）
        engine = create_engine(settings.database_url_sync)

        async def _loop() -> None:
            try:
                while True:
                    await asyncio.sleep(settings.ingest_resync_interval_seconds)
                    # 同步 DB 工作必须卸到线程，避免阻塞 FastAPI 事件循环；
                    # 用位置参数 wrapper 规避 asyncio.to_thread 不能传 keyword-only 的坑（见 CLAUDE.md）
                    await asyncio.to_thread(_run_resync_once, engine)
            except asyncio.CancelledError:
                raise

        resync_task = asyncio.create_task(_loop(), name="embedding-resync")
        logger.info(
            f"[resync] 后台补偿循环已启用：间隔 {settings.ingest_resync_interval_seconds}s，"
            f"每轮上限 {settings.ingest_resync_batch_limit} chunk"
        )
    # LangGraph postgres 检查点（仅 rag_engine=langgraph & langgraph_checkpoint=postgres 时建池+建表）；
    # memory/legacy 模式为 no-op。务必在 yield 前 await，使首个图请求能用上已初始化的 saver。
    await init_checkpointer()
    # 联网 MCP 工具（web 意图）：仅 mcp_enabled 时建连 + load 工具。init_* 对未启用/失败均 no-op；
    # 必在 yield 前 await，使首个 web 请求能用上已缓存的 _web_tools。
    if settings.mcp_enabled:
        await init_mcp_client()
        await init_web_tools()
    # 运营维护循环（M14）：仅 langgraph 启用；每轮跑 HITL 超时过期 + 检查点清理。
    maintenance_task = None
    if settings.rag_engine == "langgraph" and settings.maintenance_enabled:
        async def _maintenance_loop() -> None:
            try:
                while True:
                    await asyncio.sleep(settings.maintenance_interval_seconds)
                    try:
                        await _run_maintenance_once()
                    except Exception as e:  # noqa: BLE001  整轮失败不杀循环
                        logger.error(f"[maintenance] 维护失败 {type(e).__name__}: {e}")
            except asyncio.CancelledError:
                raise

        maintenance_task = asyncio.create_task(_maintenance_loop(), name="rag-maintenance")
        logger.info(f"[maintenance] 后台维护循环已启用：间隔 {settings.maintenance_interval_seconds}s")
    # 主动腐化巡检循环（M16）：仅 staleness_sweep_enabled 启用（不要求 langgraph，巡检纯 PG）。
    staleness_task = None
    if settings.staleness_sweep_enabled:
        async def _staleness_sweep_loop() -> None:
            try:
                while True:
                    await asyncio.sleep(settings.staleness_sweep_interval_seconds)
                    try:
                        await _run_staleness_sweep_once()
                    except Exception as e:  # noqa: BLE001  整轮失败不杀循环
                        logger.error(f"[staleness-sweep] 巡检失败 {type(e).__name__}: {e}")
            except asyncio.CancelledError:
                raise

        staleness_task = asyncio.create_task(_staleness_sweep_loop(), name="staleness-sweep")
        logger.info(
            f"[staleness-sweep] 后台巡检循环已启用：间隔 {settings.staleness_sweep_interval_seconds}s，"
            f"每轮上限 {settings.staleness_sweep_batch_size} 关系"
        )
    try:
        yield
    finally:
        if resync_task is not None:
            resync_task.cancel()
            try:
                await resync_task
            except asyncio.CancelledError:
                pass
        if maintenance_task is not None:
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
        if staleness_task is not None:
            staleness_task.cancel()
            try:
                await staleness_task
            except asyncio.CancelledError:
                pass
        if engine is not None:
            engine.dispose()
        # 关闭检查点连接池（postgres 模式）；其余模式 no-op。
        await close_checkpointer()
        # 关闭联网 MCP 客户端会话；未启用时 no-op。
        await close_mcp_client()


app = FastAPI(
    title=f"{settings.app_name} API",
    version=__version__,
    description="代码智能知识库 RAG 系统（Phase 0 脚手架）",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
async def root() -> dict:
    return {
        "app": settings.app_name,
        "version": __version__,
        "env": settings.app_env,
        "health": "/health",
        "v1": "/v1",
        "docs_note": "开发态直接访问 http://localhost:8000/docs 查看 OpenAPI（绕过 nginx）",
    }
