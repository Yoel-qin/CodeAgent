"""v1 路由聚合。模块路由随阶段递增挂载。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import chat, conversations
from app.api.v1.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(chat.router, prefix="/v1")
api_router.include_router(conversations.router, prefix="/v1")


@api_router.get("/v1")
async def v1_info() -> dict:
    """API 版本与已注册模块。"""
    return {
        "version": "v1",
        "modules": ["chat (stub)"],
        "planned": [
            "chat", "code", "graph", "communities", "sync",
            "agents", "monitor", "search", "settings", "documents", "resources",
        ],
    }
