"""M45 RBAC ORM 模型测试（零 infra）：表注册进 metadata + 关键列/索引存在 + Conversation.user_id。"""
from __future__ import annotations


def test_auth_tables_registered():
    from app.db.base import Base
    from app.db.models.auth import Role, User  # noqa: F401

    tables = Base.metadata.tables
    assert "roles" in tables and "users" in tables
    assert "allowed_kinds" in tables["roles"].c
    assert "endpoint_classes" in tables["roles"].c
    assert "password_hash" in tables["users"].c
    assert "role_id" in tables["users"].c
    assert "is_active" in tables["users"].c


def test_conversation_user_id_nullable():
    from app.db.base import Base

    col = Base.metadata.tables["conversations"].c["user_id"]
    assert col.nullable is True


def test_rbac_settings_defaults_off():
    from app.core.config import Settings

    s = Settings()
    assert s.rbac_enabled is False
    assert s.jwt_secret == ""
    assert s.jwt_expire_minutes == 720
