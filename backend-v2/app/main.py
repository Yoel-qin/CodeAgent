"""backend-v2 入口。M4 起 lifespan 挂载 MCP 工具加载（fail-soft）。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.agent.tools_loader import load_tools, reset_tools
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    try:
        await load_tools()  # 三 server 独立降级；整体再兜一层：加载失败不阻断启动
    except Exception as e:  # noqa: BLE001 —— MCP 全挂时 agent 仍可启动（运行期再降级）
        logger.error("lifespan: tools 加载失败（agent 工具侧降级）: {}", e)
    yield
    reset_tools()


app = FastAPI(title="CodeRAG-v2", lifespan=lifespan)
app.include_router(health_router)
app.include_router(chat_router)
