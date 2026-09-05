"""RBAC 依赖层（M9）：``RBAC_ENABLED`` off → 匿名伪用户透传（零行为变更，既有
端点/测试零修改）；on → JWT 解析 + 用户/角色查询 + 端点类门 + repo 可见性。

scope 归一化：JSONB 原形 → 内部统一形状
``{"repos": "*" | set[str], "kinds": {"code", "doc"}}``（kinds 含 "*" 或空 → 全集，
不惩罚漏配）；图内消费统一走 :func:`request_scopes` 产物。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from app.core.config import settings
from app.core.security import decode_token
from app.db.base import SessionLocal
from app.db.models import Role, User

__all__ = ["ANONYMOUS_USER", "ALL_KINDS", "ensure_repo_allowed", "get_current_user",
           "normalize_scopes", "repo_visible", "request_scopes", "require_class"]

#: 读域全集（图内门控的分母）
ALL_KINDS = ("code", "doc")

#: RBAC off 的匿名伪用户（全权——off 语义即「不做任何限制」）
ANONYMOUS_USER: dict = {
    "username": "anonymous", "role": "anonymous",
    "allowed_scopes": {"repos": ["*"], "kinds": list(ALL_KINDS)},
    "endpoint_classes": ["*"],
}


def normalize_scopes(scopes: dict) -> dict:
    """JSONB scopes → 内部形状（repos ``"*"`` 或集合；kinds 展开 "*"）→ 全集。"""
    repos = scopes.get("repos") or []
    kinds = scopes.get("kinds") or []
    return {
        "repos": "*" if "*" in repos else set(repos),
        "kinds": set(ALL_KINDS) if (not kinds or "*" in kinds) else set(kinds),
    }


async def get_current_user(request: Request) -> dict:
    """请求 → 用户 dict（off → ANONYMOUS；on → Bearer JWT → DB 用户）。401 语义：
    缺 token（Bearer 或 coderag_token cookie 均无）/ 坏 token / 用户不存在 / 已禁用。"""
    if not settings.rbac_enabled:
        return ANONYMOUS_USER
    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not token:  # KEEP④：Bearer 优先，缺则 httpOnly cookie（浏览器前端免手拼 header）
        token = request.cookies.get("coderag_token") or ""
    username = decode_token(token) if token else None
    if username is None:
        raise HTTPException(status_code=401, detail="未认证或凭证已过期")
    async with SessionLocal() as session:
        row = (await session.execute(
            select(User, Role).join(Role, User.role_id == Role.id)
            .where(User.username == username))).first()
    if row is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    user, role = row
    if user.disabled:
        raise HTTPException(status_code=401, detail="用户已禁用")
    return {"username": user.username, "role": role.name,
            "allowed_scopes": role.allowed_scopes or {},
            "endpoint_classes": role.endpoint_classes or []}


def require_class(cls: str):
    """router 级端点类门工厂：off → 直通；on → ``"*"`` 或命中类名，否则 403。"""
    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if settings.rbac_enabled:
            classes = user.get("endpoint_classes") or []
            if "*" not in classes and cls not in classes:
                raise HTTPException(status_code=403, detail=f"无 {cls} 端点访问权限")
        return user
    return _dep


def repo_visible(user: dict, repo: str) -> bool:
    """repo 可见性（纯判定不抛）：off / ``"*"`` / 命中列表。"""
    if not settings.rbac_enabled:
        return True
    repos = (user.get("allowed_scopes") or {}).get("repos") or []
    return "*" in repos or repo in repos


def ensure_repo_allowed(user: dict, repo: str) -> None:
    if not repo_visible(user, repo):
        raise HTTPException(status_code=403, detail=f"无仓库访问权限: {repo}")


def request_scopes(user: dict) -> dict | None:
    """→ 图 ``configurable["scopes"]`` 的归一化 scopes；RBAC off → None（零行为变更）。"""
    if not settings.rbac_enabled:
        return None
    return normalize_scopes(user.get("allowed_scopes") or {})
