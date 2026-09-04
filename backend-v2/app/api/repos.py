"""GET /v1/repos（M6 Task 1）——repos_root 下一级仓库目录名列表。

只列**目录**、跳过 ``.`` 开头隐藏目录，字典序返回；repos_root 不存在 / 是文件 /
不可读等任何 OSError → ``{"items": []}``（只读端点绝不因环境缺目录 500）。

M9：RBAC on 时按用户 ``allowed_scopes["repos"]`` 过滤（``"*"`` 全见；非列表用户
只看得见自己可见集里的仓库）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, require_class
from app.core.config import settings

router = APIRouter(prefix="/v1/repos", tags=["repos"], dependencies=[Depends(require_class("repos"))])


@router.get("")
def list_repos(user: dict = Depends(get_current_user)) -> dict:
    try:
        entries = list(Path(settings.repos_root).iterdir())
    except OSError:  # 目录缺失 / 是文件 / 不可读，一律降级为空列表
        return {"items": []}
    repos = sorted(p.name for p in entries if p.is_dir() and not p.name.startswith("."))
    if settings.rbac_enabled:
        allowed = (user.get("allowed_scopes") or {}).get("repos") or []
        if "*" not in allowed:
            allowed_set = set(allowed)
            repos = [r for r in repos if r in allowed_set]
    return {"items": repos}
