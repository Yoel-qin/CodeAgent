"""认证路由（M45）：登录换 JWT。public（不挂 require_class）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.schemas.auth import LoginRequest, LoginResponse, UserInfo
from app.services.auth_service import authenticate, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, session: AsyncSession = Depends(get_db)) -> LoginResponse:
    user = await authenticate(session, req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    role_name = user.role.name if hasattr(user, "role") and user.role else "unknown"
    return LoginResponse(
        access_token=create_access_token(user.id, role_name),
        expires_in=settings.jwt_expire_minutes,
        user=UserInfo(username=user.username, role=role_name),
    )
