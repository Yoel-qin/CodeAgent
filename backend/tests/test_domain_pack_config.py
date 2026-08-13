"""M36 config 默认值 + Conversation.target_repo 字段 + 迁移可 apply。"""
from __future__ import annotations

from app.core.config import Settings
from app.db.models.chat import Conversation


def test_config_defaults():
    s = Settings()
    assert s.domain_packs_dir == "domain_packs"
    # domain_pack_default_repo 回落 repo_path
    assert s.domain_pack_default_repo == s.repo_path


def test_conversation_has_target_repo_field():
    # target_repo 是 nullable 字段（默认 None）
    col = Conversation.__table__.columns.get("target_repo")
    assert col is not None
    assert col.nullable is True
    assert str(col.type) == "VARCHAR(256)" or str(col.type).upper() == "STRING(256)"
