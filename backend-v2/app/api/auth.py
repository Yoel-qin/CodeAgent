"""登录端点（M9）：POST /v1/auth/login → JWT + 用户载荷。公开端点（无类门）；
RBAC off → 501（提示未启用，前端据此不显示登录入口）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_token, verify_password
from app.db.base import SessionLocal
from app.db.models import Role, User

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.post("/login")
async def login(req: LoginRequest) -> dict:
    if not settings.rbac_enabled:
        raise HTTPException(status_code=501, detail="RBAC 未启用（RBAC_ENABLED=0），无需登录")
    async with SessionLocal() as session:
        row = (await session.execute(
            select(User, Role).join(Role, User.role_id == Role.id)
            .where(User.username == req.username))).first()
    if row is None or not verify_password(req.password, row[0].password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user, role = row
    if user.disabled:
        raise HTTPException(status_code=401, detail="用户已禁用")
    return {"access_token": create_token(user.username), "token_type": "bearer",
            "user": {"username": user.username, "role": role.name,
                     "allowed_scopes": role.allowed_scopes or {},
                     "endpoint_classes": role.endpoint_classes or []}}
