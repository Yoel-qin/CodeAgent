"""通用依赖（认证占位、分页等）。Phase 0 仅提供骨架。"""
from __future__ import annotations

from app.db import get_db  # noqa: F401  重导出，供路由统一 import


def pagination(page: int = 1, page_size: int = 20) -> dict:
    """分页参数（对齐 api 接口清单：page 从 1 开始，page_size 默认 20，最大 100）。"""
    page = max(page, 1)
    page_size = max(min(page_size, 100), 1)
    return {"page": page, "page_size": page_size, "offset": (page - 1) * page_size}
