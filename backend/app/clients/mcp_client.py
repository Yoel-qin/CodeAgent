"""联网 MCP 客户端 —— 让 Agent 层调用远程/在线 MCP server（web 搜索/抓取等）。

进程级单例，生命周期由 ``main.lifespan`` 管理（照搬 ``agent/memory/checkpointer.py`` 模式）：
``init_mcp_client()`` 启动时建连、``get_mcp_client()`` 请求期取已建连客户端、``close_mcp_client()``
收尾关连。``mcp_enabled`` 关 / ``mcp_servers`` 空 / 建连失败 → ``_client`` 留 None，联网工具随之返空、
web 意图回落 KB retrieve（见 ``agent/nodes/router.route``），**后端永不因 MCP 崩**。

延迟 import（``MultiServerMCPClient``）放在 ``init_mcp_client`` 内，避免未启用时无谓加载
langchain-mcp-adapters。transport 走 ``sse`` / ``streamable_http``（联网场景，不用 stdio）。

> API：``langchain_mcp_adapters.client.MultiServerMCPClient({name: {"url":..., "transport":...}})``，
> ``__aenter__`` 激活会话后 ``await client.get_tools() -> list[BaseTool]``。版本/字段以实现期核实为准；
> 若 API 与此不符，本模块 + ``agent/tools/web_tools.py`` 是唯一受影响处。
"""
from __future__ import annotations

import json

from loguru import logger

from app.core.config import settings

# MultiServerMCPClient 实例（__aenter__ 后）；未启用/未初始化/启动失败 → None。
_client = None
# 构造出的客户端本身（async 上下文管理器）：close 时经 __aexit__ 关会话。
_cm = None


def get_mcp_client():
    """返回已建连的 MCP 客户端；未启用/未初始化/启动失败 → None（调用方据此优雅降级，不抛）。

    注意：与 ``checkpointer.get_checkpointer`` 不同，此处**不**抛 RuntimeError——MCP 是可选增强，
    None 即「该特性关闭」，调用方（``web_tools`` / ``router``）按空集降级。
    """
    return _client


async def init_mcp_client() -> None:
    """lifespan 启动调：解析 ``mcp_servers`` JSON → 建 ``MultiServerMCPClient`` → ``__aenter__`` 激活。

    ``mcp_enabled`` 关 / 配置空/非法 / 任何连接异常 → no-op（``_client`` 留 None），应用照常启动。
    """
    global _client, _cm
    if not settings.mcp_enabled:
        return
    raw = (settings.mcp_servers or "").strip()
    if not raw:
        logger.warning("[mcp] mcp_enabled=true 但 mcp_servers 为空，跳过联网工具初始化")
        return
    try:
        servers_list = json.loads(raw)
        if not isinstance(servers_list, list) or not servers_list:
            raise ValueError("mcp_servers 须为非空 JSON 数组")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[mcp] mcp_servers 解析失败 {type(e).__name__}: {e}（联网工具禁用）")
        return
    # → MultiServerMCPClient 期望的 {name: {"url":..., "transport":...}} 形态
    servers: dict[str, dict[str, str]] = {}
    for s in servers_list:
        if not isinstance(s, dict) or "url" not in s:
            continue
        name = s.get("name") or s["url"]
        servers[name] = {"url": s["url"], "transport": s.get("transport", "sse")}
    if not servers:
        logger.error("[mcp] mcp_servers 解析后无有效条目，联网工具禁用")
        return
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        cm = MultiServerMCPClient(servers)
        client = await cm.__aenter__()  # 激活会话（持久，跨请求复用）
        _cm, _client = cm, client
        logger.info(f"[mcp] MCP 客户端已连接：{list(servers)}")
    except Exception as e:  # noqa: BLE001  不可达/协议错 → 不拖垮后端
        logger.error(
            f"[mcp] MCP 客户端连接失败 {type(e).__name__}: {e}"
            "（联网工具禁用，web 意图将回落 KB 检索）"
        )
        _client, _cm = None, None


async def close_mcp_client() -> None:
    """lifespan 收尾调：退出客户端上下文管理器（关会话）、清引用。未启用时 no-op。"""
    global _client, _cm
    cm, _client, _cm = _cm, None, None
    if cm is not None:
        try:
            await cm.__aexit__(None, None, None)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[mcp] MCP 客户端关闭失败 {type(e).__name__}: {e}")
