"""M36 build_whitelist：激活包 config_registry → CitationEnforcer whitelist 谓词。"""
from __future__ import annotations

from app.domain_packs.models import ConfigItem, DomainPack, Manifest
from app.domain_packs.registry import build_whitelist


def _pack(items: list[ConfigItem]) -> DomainPack:
    return DomainPack(manifest=Manifest(name="x", target_repo="x/y"), config_registry=items)


def test_build_whitelist_hits():
    pack = _pack([ConfigItem(key="max_reconsume_times"), ConfigItem(key="consume_thread_min")])
    wl = build_whitelist(pack)
    assert wl is not None
    assert wl("max_reconsume_times") is True       # 命中
    assert wl("MAX_RECONSUME_TIMES") is True        # 大小写不敏感
    assert wl("unknown_key") is False


def test_build_whitelist_empty_returns_none():
    pack = _pack([])   # 空 config_registry
    assert build_whitelist(pack) is None


def test_build_whitelist_none_input():
    assert build_whitelist(None) is None
