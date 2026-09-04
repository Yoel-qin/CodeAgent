"""RBAC 两表 ORM：Role / User（M9）。

- ``allowed_scopes`` 形状冻结：``{"repos": ["*"|repo, ...], "kinds": ["*"|"code"|"doc"]}``
  ——repo 可见性 + code/doc 读域两维度（spec §8.3）；``endpoint_classes`` = ``["*"]``
  或 router 名列表（chat/repos/documents/graph/reader/sync/monitor/eval）。
- roles 由迁移 v2_0008 seed（admin/developer/ops/external）；用户只经
  ``scripts/create_user.py`` 建（无注册端点——无默认密码后门）。
"""
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_pg_jsonb = JSON().with_variant(JSONB, "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("name", name="uk_roles_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    allowed_scopes: Mapped[dict] = mapped_column(_pg_jsonb, default=dict)
    endpoint_classes: Mapped[list] = mapped_column(_pg_jsonb, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=_utcnow
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uk_users_username"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(128))  # bcrypt $2b$ 固定 60 字符，留余量
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("roles.id"), index=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=_utcnow
    )
