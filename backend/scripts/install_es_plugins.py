"""安装 ES IK 分词插件（M31，spec §3.2）：下载 infinilabs 官方发布中心 zip →
解压到 docker/es/plugins/analysis-ik/（compose 已挂载为 ES plugins 卷）。

纯 stdlib（urllib/zipfile/pathlib），幂等：目标已有 plugin-descriptor.properties 则 skip。
URL 不走 github（被墙）——release.infinilabs.com 国内直连可达（2026-08-23 实测）。

用法（从 backend/ 运行）::
  uv run python scripts/install_es_plugins.py
  docker compose restart elasticsearch   # ES 启动期扫描 plugins/ 自动装载
"""
from __future__ import annotations

import os
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_URL = "https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.11.0.zip"
_TARGET = Path(__file__).resolve().parents[2] / "docker" / "es" / "plugins" / "analysis-ik"


def main() -> int:
    marker = _TARGET / "plugin-descriptor.properties"
    if marker.exists():
        print(f"skip: {_TARGET} 已存在插件（{marker.name}）")
        return 0
    _TARGET.mkdir(parents=True, exist_ok=True)

    print(f"下载: {_URL}")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        urllib.request.urlretrieve(_URL, tmp.name)
        zip_path = tmp.name
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if not any(n.endswith("plugin-descriptor.properties") for n in names):
                print("错误: zip 内无 plugin-descriptor.properties（非插件包）", file=sys.stderr)
                return 1
            zf.extractall(_TARGET)
    finally:
        os.unlink(zip_path)

    # 兼容 zip 内多包一层 elasticsearch/ 目录的形状（官方包顶层直出，此为防御）
    inner = _TARGET / "elasticsearch"
    if not marker.exists() and (inner / "plugin-descriptor.properties").exists():
        for p in inner.iterdir():
            p.rename(_TARGET / p.name)
        inner.rmdir()

    if not marker.exists():
        print(f"错误: 解压后未找到 {marker}", file=sys.stderr)
        return 1
    print(f"已安装到: {_TARGET}")
    print("请执行: docker compose restart elasticsearch（启动期扫描 plugins/ 自动装载）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
