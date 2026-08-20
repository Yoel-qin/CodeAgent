"""v1 路由聚合。模块路由随阶段递增挂载。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agents,
    auth,
    chat,
    conversations,
    documents,
    eval,
    graph,
    monitor,
    resources,
    search,
    staleness,
    sync,
)
from app.api.v1.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth.router, prefix="/v1")
api_router.include_router(chat.router, prefix="/v1")
api_router.include_router(conversations.router, prefix="/v1")
api_router.include_router(sync.router, prefix="/v1")
api_router.include_router(documents.router, prefix="/v1")
api_router.include_router(resources.router, prefix="/v1")
api_router.include_router(graph.router, prefix="/v1")
api_router.include_router(agents.router, prefix="/v1")
api_router.include_router(staleness.router, prefix="/v1")
api_router.include_router(monitor.router, prefix="/v1")
api_router.include_router(search.router, prefix="/v1")
api_router.include_router(eval.router, prefix="/v1")


@api_router.get("/v1")
async def v1_info() -> dict:
    """API 版本与已注册模块。"""
    return {
        "version": "v1",
        "modules": ["auth", "chat (stub)", "sync", "documents", "resources", "graph", "agents", "staleness", "monitor", "search", "eval"],
        "planned": [
            "chat", "code", "sync",
            "settings",
        ],
    }
