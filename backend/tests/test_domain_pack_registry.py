"""M36 DomainPackRegistry + init 单测——纯函数，无 infra。"""
from __future__ import annotations

from app.domain_packs import registry as reg_mod
from app.domain_packs.models import Manifest
from app.domain_packs.registry import DomainPackRegistry, get_registry, init_domain_pack_registry


def _pack(name: str, target_repo: str):
    from app.domain_packs.models import DomainPack
    return DomainPack(manifest=Manifest(name=name, target_repo=target_repo))


def test_register_idempotent_and_get():
    r = DomainPackRegistry()
    r.register(_pack("a", "org/a"))
    r.register(_pack("a", "org/a2"))   # 同名覆盖
    assert r.get("a").manifest.target_repo == "org/a2"
    assert r.get("missing") is None


def test_active_for_repo_match_and_none():
    r = DomainPackRegistry()
    r.register(_pack("rocketmq", "apache/rocketmq"))
    assert r.active_for_repo("apache/rocketmq").manifest.name == "rocketmq"
    assert r.active_for_repo("other/repo") is None     # 无匹配
    assert r.active_for_repo(None) is None              # None 不激活（resolve 层先回落默认，见 spec §7）


def test_packs_list():
    r = DomainPackRegistry()
    r.register(_pack("a", "org/a"))
    r.register(_pack("b", "org/b"))
    assert {p.manifest.name for p in r.packs()} == {"a", "b"}


def test_init_scans_loads_and_skips_bad(tmp_path, monkeypatch):
    # 两个有效包目录 + 一个畸形（无 manifest）
    (tmp_path / "good1").mkdir()
    (tmp_path / "good1" / "manifest.yaml").write_text("name: g1\ntarget_repo: org/g1\n", encoding="utf-8")
    (tmp_path / "good2").mkdir()
    (tmp_path / "good2" / "manifest.yaml").write_text("name: g2\ntarget_repo: org/g2\n", encoding="utf-8")
    (tmp_path / "bad").mkdir()   # 无 manifest → 跳过
    # init 用独立 registry，避免污染模块单例
    test_registry = DomainPackRegistry()
    monkeypatch.setattr(reg_mod, "_registry", test_registry, raising=False)
    init_domain_pack_registry(packs_dir=tmp_path)
    assert test_registry.get("g1") is not None
    assert test_registry.get("g2") is not None
    assert test_registry.get("bad") is None   # 畸形包跳过，不抛


def test_get_registry_singleton():
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2
