"""backend-v2 入口。M4 起 lifespan 挂载 MCP 工具加载（fail-soft）。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.agent import tools_loader
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.graph import router as graph_router
from app.api.health import router as health_router
from app.api.reader import router as reader_router
from app.api.repos import router as repos_router
from app.api.sync import router as sync_router
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    try:
        # 模块属性调用：测试只需钉 tools_loader.load_tools 一处（直引符号需双钉，Task 10 评审遗留）
        await tools_loader.load_tools()  # 三 server 独立降级；整体再兜一层：加载失败不阻断启动
    except Exception as e:  # noqa: BLE001 —— MCP 全挂时 agent 仍可启动（运行期再降级）
        logger.error("lifespan: tools 加载失败（agent 工具侧降级）: {}", e)
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
