"""认证服务（M45）：bcrypt 密码 + HS256 JWT（python-jose / passlib 均为既有依赖）。

JWT payload：{sub: str(user_id), role, exp}。无状态校验（不查库）在 deps 层组合；
本模块 authenticate 负责登录时的密码校验（查 users + join roles）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.auth import User

_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(pw: str) -> str:
    return _ctx.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    return _ctx.verify(pw, hashed)


def create_access_token(user_id: int, role: str, *, expires_minutes: int | None = None) -> str:
    exp = datetime.now(UTC) + timedelta(minutes=expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes)
    return jwt.encode({"sub": str(user_id), "role": role, "exp": exp},
                      settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """验签 + 过期校验；坏 token → ValueError（调用方转 401）。"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError as e:
        raise ValueError(f"invalid token: {e}") from e


async def authenticate(session: AsyncSession, username: str, password: str) -> User | None:
    """用户名密码校验（join roles）。用户不存在/密码错/停用 → None（不区分，防枚举）。"""
    from app.db.models.auth import Role  # 延迟导入避免与 auth 模型循环

    row = (await session.execute(
        select(User).where(User.username == username).join(Role, User.role_id == Role.id)
    )).scalars().first()
    if row is None or not row.is_active:
        return None
    if not verify_password(password, row.password_hash):
        return None
    return row
