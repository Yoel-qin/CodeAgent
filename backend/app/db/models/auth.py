"""RBAC 用户/角色模型（M45）。

roles 两权限维度均为 JSONB 字符串列表，``["*"]`` 通配（deps 层归一化为 None=不限制）：
  - allowed_kinds：可见 chunk kind（运行时全集 code/doc/table/image）
  - endpoint_classes：可用端点类（chat/search/graph/readops/writeops）
内置 4 角色由 m45rbac 迁移幂等 seed：admin / developer / ops / external。
users 密码存 bcrypt 哈希（passlib），绝不存明文。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    allowed_kinds: Mapped[dict] = mapped_column(JSONB, default=list)
    endpoint_classes: Mapped[dict] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("roles.id", name="fk_users_role_id_roles"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # lazy="joined"：async 会话不允许属性惰性加载（MissingGreenlet），
    # 登录/get_current_user 都要读 role.name/权限，随主查询 JOIN 预载（M45）。
    role: Mapped[Role] = relationship(Role, lazy="joined")
