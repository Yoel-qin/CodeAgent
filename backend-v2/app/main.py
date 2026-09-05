"""backend-v2 入口。M4 起 lifespan 挂载 MCP 工具加载（fail-soft）。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.agent import tools_loader
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.eval import router as eval_router
from app.api.graph import router as graph_router
from app.api.health import router as health_router
from app.api.monitor import router as monitor_router
from app.api.reader import router as reader_router
from app.api.repos import router as repos_router
from app.api.sync import router as sync_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.services.eval_service import reclaim_orphan_runs


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    if settings.rbac_enabled and not settings.jwt_secret:
        raise RuntimeError("RBAC_ENABLED=1 需要 JWT_SECRET（启动 fail-fast，防弱密钥静默上线）")
    try:
        # 模块属性调用：测试只需钉 tools_loader.load_tools 一处（直引符号需双钉，Task 10 评审遗留）
        await tools_loader.load_tools()  # 各组独立降级；整体再兜一层：加载失败不阻断启动
    except Exception as e:  # noqa: BLE001 —— MCP 全挂时 agent 仍可启动（运行期再降级）
        logger.error("lifespan: tools 加载失败（agent 工具侧降级）: {}", e)
    try:
        reclaimed = await reclaim_orphan_runs()
        if reclaimed:
            logger.info("lifespan: 回收 {} 条 RUNNING 悬挂 eval_runs", reclaimed)
    except Exception as e:  # noqa: BLE001 —— 回收失败（表未建/DB 不可达）不阻断启动
        logger.warning("lifespan: eval_runs 孤儿回收跳过: {}", e)
    yield
    tools_loader.reset_tools()


app = FastAPI(title="CodeRAG-v2", lifespan=lifespan)
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(sync_router)
app.include_router(repos_router)
app.include_router(documents_router)
app.include_router(graph_router)
app.include_router(reader_router)
app.include_router(monitor_router)
app.include_router(eval_router)
app.include_router(auth_router)
