"""密码哈希与 JWT（M9）。bcrypt 直用（不经 passlib——v1 的 ``bcrypt<4.0.0`` pin 是
passlib 后端检测特有的坑，直用无此坑）；JWT HS256（pyjwt）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import settings

__all__ = ["create_token", "decode_token", "hash_password", "verify_password"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:  # 坏哈希（长度/字符集不符）→ False，不抛
        return False


def create_token(username: str) -> str:
    now = datetime.now(UTC)
    payload = {"sub": username, "iat": now,
               "exp": now + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> str | None:
    """验签+验期 → username；任何失败（坏签名/过期/缺 sub）→ None。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None
