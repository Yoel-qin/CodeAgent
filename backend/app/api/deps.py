"""通用依赖：DB 会话、分页、RBAC 鉴权（M45）。

RBAC off → ANONYMOUS 伪用户透传（零 DB 查询、零行为变更）；
on → Authorization: Bearer JWT 验签 → 查 users+roles → is_active 校验。
require_class(cls) 为依赖工厂，router 级挂载（api/v1/router.py），端点函数零改动。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import get_db  # noqa: F401  重导出，供路由统一 import
from app.db.models.auth import User


@dataclass(frozen=True)
class CurrentUser:
    """请求级用户视图。None = 不限制（匿名或 DB ["*"] 归一化）。"""

    id: int | None
    username: str
    role: str
    allowed_kinds: set[str] | None
    endpoint_classes: set[str] | None

    @property
    def is_admin(self) -> bool:
        return self.id is None or self.role == "admin"


ANONYMOUS = CurrentUser(id=None, username="anonymous", role="anonymous",
                        allowed_kinds=None, endpoint_classes=None)


def _norm_perms(raw: list | None) -> set[str] | None:
    """JSONB 列表 → set；["*"] 或空 → None（不限制）。"""
    if not raw or "*" in raw:
        return None
    return set(raw)


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if not settings.rbac_enabled:
        return ANONYMOUS
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供访问令牌")
    from app.services.auth_service import decode_token

    try:
        payload = decode_token(auth[len("Bearer "):])
        user_id = int(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(status_code=401, detail="访问令牌无效或已过期") from None
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="访问令牌无效或已过期")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")
    role = user.role  # ORM relationship
    return CurrentUser(
        id=user.id, username=user.username, role=role.name,
        allowed_kinds=_norm_perms(role.allowed_kinds),
        endpoint_classes=_norm_perms(role.endpoint_classes),
    )


def require_class(cls: str) -> Callable[[CurrentUser], None]:
    """端点类门（chat/search/graph/readops/writeops）。匿名（off）全通过。"""

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> None:
        if user.endpoint_classes is None or cls in user.endpoint_classes:
            return
        raise HTTPException(status_code=403, detail=f"角色 {user.role} 无权访问该资源")

    return _dep


def ensure_owner(user: CurrentUser | None, owner_id: int | None) -> None:
    """对话属主校验：None（服务层默认）/匿名/off 时期历史（NULL）/本人/admin 放行，
    否则 404（不暴露存在性）。"""
    if user is None or user.id is None or owner_id is None \
            or user.is_admin or owner_id == user.id:
        return
    raise HTTPException(status_code=404, detail="会话不存在")


def pagination(page: int = 1, page_size: int = 20) -> dict:
    """分页参数（对齐 api 接口清单：page 从 1 开始，page_size 默认 20，最大 100）。"""
    page = max(page, 1)
    page_size = max(min(page_size, 100), 1)
    return {"page": page, "page_size": page_size, "offset": (page - 1) * page_size}
