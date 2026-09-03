"""GET /v1/repos（M6 Task 1）——repos_root 下一级仓库目录名列表。

只列**目录**、跳过 ``.`` 开头隐藏目录，字典序返回；repos_root 不存在 / 是文件 /
不可读等任何 OSError → ``{"items": []}``（只读端点绝不因环境缺目录 500）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(prefix="/v1/repos", tags=["repos"])


@router.get("")
def list_repos() -> dict:
    try:
        entries = list(Path(settings.repos_root).iterdir())
    except OSError:  # 目录缺失 / 是文件 / 不可读，一律降级为空列表
        return {"items": []}
    return {
        "items": sorted(
            p.name for p in entries if p.is_dir() and not p.name.startswith(".")
        )
    }
