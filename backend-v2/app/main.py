"""backend-v2 入口。M0：lifespan 仅日志；后续计划在这里挂 MCP client / pipeline worker。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield


app = FastAPI(title="CodeRAG-v2", lifespan=lifespan)
app.include_router(health_router)
