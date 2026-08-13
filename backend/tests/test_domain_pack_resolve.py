"""M36 resolve_active_pack + whitelist 注入——纯函数 mock，无 infra。"""
from __future__ import annotations

from types import SimpleNamespace

from app.agent.streaming import resolve_active_pack
from app.domain_packs import registry as reg_mod
from app.domain_packs.models import DomainPack, Manifest


def _conv(target_repo):
    return SimpleNamespace(target_repo=target_repo)


def _pack(name, repo, items=None):
    return DomainPack(
        manifest=Manifest(name=name, target_repo=repo),
        config_registry=items or [],
    )


def test_resolve_match(tmp_path, monkeypatch):
    r = reg_mod.DomainPackRegistry()
    r.register(_pack("rocketmq", "apache/rocketmq"))
    monkeypatch.setattr(reg_mod, "_registry", r)
    pack = resolve_active_pack(_conv("apache/rocketmq"))
    assert pack is not None
    assert pack.manifest.name == "rocketmq"


def test_resolve_no_match_returns_none(monkeypatch):
    r = reg_mod.DomainPackRegistry()
    r.register(_pack("rocketmq", "apache/rocketmq"))
    monkeypatch.setattr(reg_mod, "_registry", r)
    assert resolve_active_pack(_conv("other/repo")) is None


def test_resolve_null_target_repo_falls_back_default(monkeypatch):
    """conv.target_repo=None → 回落 settings.domain_pack_default_repo (or repo_path)。"""
    import app.agent.streaming as sm
    r = reg_mod.DomainPackRegistry()
    r.register(_pack("rocketmq", "apache/rocketmq"))
    monkeypatch.setattr(reg_mod, "_registry", r)
    monkeypatch.setattr(sm.settings, "domain_pack_default_repo", "apache/rocketmq")
    assert resolve_active_pack(_conv(None)).manifest.name == "rocketmq"
    # 默认 repo 也不匹配 → None
    monkeypatch.setattr(sm.settings, "domain_pack_default_repo", "")
    monkeypatch.setattr(sm.settings, "repo_path", "some/sample")
    assert resolve_active_pack(_conv(None)) is None


def test_resolve_empty_registry_returns_none(monkeypatch):
    monkeypatch.setattr(reg_mod, "_registry", reg_mod.DomainPackRegistry())  # 空 registry
    assert resolve_active_pack(_conv("apache/rocketmq")) is None
