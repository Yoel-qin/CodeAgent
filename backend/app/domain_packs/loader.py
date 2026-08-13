"""M36 DomainPack loader：读 manifest + 4 个领域 yaml + prompts/*.md → DomainPack。

manifest 缺失 / pydantic 校验失败 → PackLoadError（由 init 注册期 catch 跳过）。
缺失的领域 yaml 文件 → 该字段空列表（容错，骨架包不必全有）。
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.domain_packs.models import DomainPack


class PackLoadError(Exception):
    """领域包加载失败（manifest 缺失 / 校验失败 / IO 错）。"""


_DOMAIN_YAMLS = ("trace_templates", "diagnosis_trees", "tuning_rules", "config_registry")


def _read_yaml(path: Path) -> object:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_pack(pack_dir: Path) -> DomainPack:
    """从目录加载一个领域包。pack_dir 须含 manifest.yaml；领域 yaml 与 prompts 可选。"""
    manifest_path = pack_dir / "manifest.yaml"
    if not manifest_path.is_file():
        raise PackLoadError(f"missing manifest.yaml in {pack_dir}")
    try:
        data: dict = {"manifest": _read_yaml(manifest_path)}
        for name in _DOMAIN_YAMLS:
            p = pack_dir / f"{name}.yaml"
            if p.is_file():
                data[name] = _read_yaml(p) or []
        # prompts/*.md
        prompts_dir = pack_dir / "prompts"
        if prompts_dir.is_dir():
            data["prompts"] = {
                p.name: p.read_text(encoding="utf-8")
                for p in prompts_dir.glob("*.md")
            }
        return DomainPack.model_validate(data)
    except ValidationError as e:
        raise PackLoadError(f"invalid pack {pack_dir.name}: {e}") from e
    except (OSError, yaml.YAMLError) as e:
        raise PackLoadError(f"read error in {pack_dir.name}: {e}") from e
