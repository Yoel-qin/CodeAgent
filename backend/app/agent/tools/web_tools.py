"""联网（MCP）Agent 工具集。

远程 MCP server 暴露的工具经 ``langchain-mcp-adapters`` 加载为 ``BaseTool``；本模块在 lifespan
启动期一次性 load + 包一层可观测性 wrapper（统一发 ``agent_step``，**不发 citation**——联网结果按
既定决策只走轨迹，前端零改动），缓存到模块全局 ``_web_tools``。请求期 ``get_web_tools()`` 纯同步读。

设计契合点（见 CLAUDE.md / 计划）：现有 Agent 的 ``TOOLS`` 是模块级常量、``create_react_agent`` 是
惰性单例、``_base.build_agent`` 是同步 ``Callable``，故 MCP 工具必须在**启动时**加载缓存，请求期不触网、
不改 ``run_scenario_agent`` 签名、不碰 ``RunnableConfig.configurable``。

降级链：MCP 未启用/不可达/加载失败 → ``_web_tools=[]`` → ``router.route`` 把 web 意图回落 ``retrieve``。

事件 helper（``_safe_writer``/``_emit_step``）本模块自带一份（与 ``code_tools``/``doc_tools`` 同款，
各 tools 模块各自定义，避免相互 import）。
"""
from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.config import get_stream_writer
from loguru import logger

from app.clients.mcp_client import get_mcp_client

_web_tools: list[BaseTool] = []


# ---- 事件 helper（与 code_tools 同款；模块自带，不跨 tools 模块 import）----


def _safe_writer():
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001
        return None


def _emit_step(name: str, args: dict, n: int) -> None:
    if (w := _safe_writer()) is None:
        return
    try:
        w({"event": "agent_step", "data": {"tool": name, "args": args, "n": n}})
    except Exception:  # noqa: BLE001
        pass


# ---- 包一层可观测性 wrapper ----


def _wrap_for_step(tool: BaseTool) -> BaseTool:
    """包一层远程 MCP 工具：调用前后发 ``agent_step``，失败降级为文本提示（单工具失败不杀 Agent）。

    schema（name/description/args_schema）透传自远程工具，LLM 看到的接口与原工具一致；仅 ``_arun``
    拦截一层。同步 ``_run`` 不可用——联网工具仅异步调用（create_react_agent 走 ainvoke）。
    """
    name = tool.name

    async def _arun(**kwargs):  # noqa: ANN202
        try:
            result = await tool.ainvoke(kwargs)
        except Exception as e:  # noqa: BLE001
            _emit_step(name, kwargs, 0)
            return f"[联网工具 {name} 调用失败：{type(e).__name__}: {e}]"
        _emit_step(name, kwargs, 1)
        return result

    def _sync_unavailable(*args, **kwargs):  # noqa: ANN001, ANN002, ANN202
        raise NotImplementedError(f"联网工具 {name} 仅支持异步调用")

    return StructuredTool.from_function(
        _sync_unavailable,
        name=name,
        description=tool.description or "",
        args_schema=tool.args_schema,
        coroutine=_arun,
    )


# ---- 生命周期：启动期 load+wrap+缓存；请求期同步读 ----


async def init_web_tools() -> None:
    """lifespan 启动调一次：load 远程工具 + wrap + 缓存。无客户端/失败 → 置空（降级）。"""
    global _web_tools
    client = get_mcp_client()
    if client is None:
        _web_tools = []
        return
    try:
        remote = await client.get_tools()
        _web_tools = [_wrap_for_step(t) for t in remote]
        logger.info(
            f"[mcp] 联网工具加载完成：{len(_web_tools)} 个 → {[t.name for t in _web_tools]}"
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"[mcp] 联网工具加载失败 {type(e).__name__}: {e}（web 意图将回落 KB 检索）")
        _web_tools = []


def get_web_tools() -> list[BaseTool]:
    """请求期同步读缓存的联网工具集（启动前/未启用/失败 → []）。"""
    return _web_tools
