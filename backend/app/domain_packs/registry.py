"""M36 DomainPackRegistry：领域包注册表 + 激活解析 + whitelist 构造 + lifespan 初始化。

复用 AgentRegistry（M33）的 register/get + 模块单例模式；但领域包由 lifespan 显式
load_pack + register（非 import 副作用）。init 扫描 domain_packs/，逐包加载，失败跳过+log。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from app.core.config import settings
from app.domain_packs.loader import PackLoadError, load_pack
from app.domain_packs.models import DomainPack

logger = logging.getLogger(__name__)


class DomainPackRegistry:
    """领域包注册表。register 按 manifest.name 幂等（同名覆盖）。"""

    def __init__(self) -> None:
        self._by_name: dict[str, DomainPack] = {}

    def register(self, pack: DomainPack) -> None:
        self._by_name[pack.manifest.name] = pack

    def get(self, name: str) -> DomainPack | None:
        return self._by_name.get(name)

    def packs(self) -> list[DomainPack]:
        return list(self._by_name.values())

    def active_for_repo(self, target_repo: str | None) -> DomainPack | None:
        """按 manifest.target_repo 匹配；None/无匹配 → None。
        一个 repo 多包取首个（M36 单包场景）。resolve 层会先把 null target_repo 回落默认 repo。"""
        if not target_repo:
            return None
        for pack in self._by_name.values():
            if pack.manifest.target_repo == target_repo:
                return pack
        return None


_registry = DomainPackRegistry()


def get_registry() -> DomainPackRegistry:
    """进程级单例（lifespan 的 init_domain_pack_registry 填充）。"""
    return _registry


def build_whitelist(pack: DomainPack | None) -> Callable[[str], bool] | None:
    """激活包 config_registry 非空 → 返回大小写不敏感命中谓词；空/None → None。"""
    if pack is None or not pack.config_registry:
        return None
    keys = {item.key.lower() for item in pack.config_registry}
    return lambda identifier: identifier.lower() in keys


def init_domain_pack_registry(packs_dir: Path | str | None = None) -> None:
    """lifespan 调用：扫描 domain_packs/ 子目录，逐包 load_pack + register。
    失败包（畸形/无 manifest/校验失败）→ catch + log warning 跳过，不抛（其余包继续）。"""
    base = Path(packs_dir) if packs_dir else Path(settings.domain_packs_dir)
    if not base.is_dir():
        logger.info("[domain-pack] 目录不存在或为空：%s（无领域包激活，通用行为）", base)
        return
    loaded = 0
    for sub in sorted(base.iterdir()):
        if not sub.is_dir():
            continue
        try:
            pack = load_pack(sub)
            _registry.register(pack)
            loaded += 1
            logger.info("[domain-pack] 已加载包 %s（target_repo=%s）",
                        pack.manifest.name, pack.manifest.target_repo)
        except PackLoadError as e:
            logger.warning("[domain-pack] 跳过包 %s：%s", sub.name, e)
    logger.info("[domain-pack] 加载完成：%d 个包", loaded)
