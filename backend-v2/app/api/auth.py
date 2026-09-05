"""登录端点（M9）：POST /v1/auth/login → JWT + 用户载荷。公开端点（无类门）；
RBAC off → 501（提示未启用，前端据此不显示登录入口）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
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
async def login(req: LoginRequest, response: Response) -> dict:
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
    token = create_token(user.username)
    # KEEP④：HttpOnly cookie（XSS 不可读；dev 为 http 不加 Secure；SameSite=Lax
    # 挡跨站 POST）。body 仍带 token 供 API 调试。
    response.set_cookie(key="coderag_token", value=token,
                        httponly=True, samesite="lax", path="/")
    return {"access_token": token, "token_type": "bearer",
            "user": {"username": user.username, "role": role.name,
                     "allowed_scopes": role.allowed_scopes or {},
                     "endpoint_classes": role.endpoint_classes or []}}


@router.post("/logout")
async def logout(response: Response) -> dict:
    """登出：无条件清 cookie（httpOnly 前端删不掉，必须后端配合；无认证依赖
    ——清 cookie 永远成功）。RBAC off → 501（对齐 login）。"""
    if not settings.rbac_enabled:
        raise HTTPException(status_code=501, detail="RBAC 未启用（RBAC_ENABLED=0），无需登出")
    response.delete_cookie("coderag_token", path="/")
    return {"ok": True}
