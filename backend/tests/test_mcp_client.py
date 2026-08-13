"""联网 MCP 客户端单测（clients/mcp_client.py）——纯/mock，无网络/无 langchain-mcp-adapters。

覆盖：``get_mcp_client`` 未初始化返 None；``init_mcp_client`` 的各 no-op/降级分支
（关 / 空配置 / 非法 JSON / 连接失败）；以及连接成功 → close 的生命周期
（sys.modules 注入 fake ``MultiServerMCPClient``，故**不依赖**真实包是否安装）。
"""
from __future__ import annotations

import sys
import types

from app.clients import mcp_client as mc
from app.core.config import settings


def _install_fake_mcp(monkeypatch, client_cls) -> None:
    """注入 fake langchain_mcp_adapters.client 模块（含父包），使 init 内的延迟 import 命中假实现。"""
    mod = types.ModuleType("langchain_mcp_adapters.client")
    mod.MultiServerMCPClient = client_cls
    parent = types.ModuleType("langchain_mcp_adapters")
    parent.client = mod
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters", parent)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", mod)


# ---- get_mcp_client ----


def test_get_mcp_client_none_when_uninit(monkeypatch):
    monkeypatch.setattr(mc, "_client", None)
    assert mc.get_mcp_client() is None


# ---- init: no-op / 降级分支（_client 必须留 None，不抛）----


async def test_init_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "mcp_enabled", False)
    monkeypatch.setattr(mc, "_client", None)
    monkeypatch.setattr(mc, "_cm", None)
    await mc.init_mcp_client()
    assert mc._client is None
    assert mc._cm is None


async def test_init_noop_when_servers_empty(monkeypatch):
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_servers", "")
    monkeypatch.setattr(mc, "_client", None)
    monkeypatch.setattr(mc, "_cm", None)
    await mc.init_mcp_client()
    assert mc._client is None


async def test_init_noop_when_bad_json(monkeypatch):
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_servers", "not-json")
    monkeypatch.setattr(mc, "_client", None)
    monkeypatch.setattr(mc, "_cm", None)
    await mc.init_mcp_client()  # 非法 JSON → 不抛
    assert mc._client is None


async def test_init_noop_when_no_valid_entries(monkeypatch):
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_servers", '[{"transport": "sse"}]')  # 无 url
    monkeypatch.setattr(mc, "_client", None)
    await mc.init_mcp_client()
    assert mc._client is None


async def test_init_graceful_on_connect_failure(monkeypatch):
    """__aenter__ 抛错 → 不拖垮后端，_client 留 None。"""
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_servers", '[{"name":"d","url":"http://x/sse"}]')

    class BoomClient:
        def __init__(self, servers):
            pass

        async def __aenter__(self):
            raise ConnectionError("unreachable")

        async def __aexit__(self, *exc):
            pass

    _install_fake_mcp(monkeypatch, BoomClient)
    monkeypatch.setattr(mc, "_client", None)
    monkeypatch.setattr(mc, "_cm", None)
    await mc.init_mcp_client()  # 连接失败 → 不抛
    assert mc._client is None
    assert mc._cm is None


# ---- init + close 生命周期（fake 连接成功）----


async def test_init_close_lifecycle(monkeypatch):
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(
        settings, "mcp_servers",
        '[{"name":"demo","url":"http://x/sse","transport":"sse"}]',
    )
    monkeypatch.setattr(mc, "_client", None)
    monkeypatch.setattr(mc, "_cm", None)

    seen: dict = {}

    class FakeClient:
        def __init__(self, servers):
            seen["servers"] = servers
            self.exited = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            self.exited = True

    _install_fake_mcp(monkeypatch, FakeClient)

    await mc.init_mcp_client()
    # 客户端经 __aenter__ 注入、servers 字典透传（name→{url,transport}）
    assert mc._client is mc._cm
    assert "demo" in seen["servers"]
    assert seen["servers"]["demo"]["transport"] == "sse"

    cm_ref = mc._cm
    await mc.close_mcp_client()
    assert cm_ref.exited is True
    assert mc._client is None
    assert mc._cm is None


async def test_close_noop_when_uninit(monkeypatch):
    monkeypatch.setattr(mc, "_client", None)
    monkeypatch.setattr(mc, "_cm", None)
    await mc.close_mcp_client()  # 不应抛
    assert mc._client is None
    assert mc._cm is None
