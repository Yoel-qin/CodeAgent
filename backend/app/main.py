"""CodeRAG FastAPI 入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield


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
