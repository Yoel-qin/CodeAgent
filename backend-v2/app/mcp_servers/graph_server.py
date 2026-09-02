"""graph-mcp server：4 个只读调用图工具的 FastMCP 薄包装。

- 每工具超时 5s（图查询可能涉及递归 CTE）
- core 纯函数经 asyncio.to_thread（**位置参数**——to_thread 不支持 kwargs）+ wait_for
- 运行：python -m app.mcp_servers.graph_server            → streamable-http :8112/mcp
        python -m app.mcp_servers.graph_server --stdio    → stdio（测试/同机 sidecar）
"""
import argparse
import asyncio
import sys

from mcp.server.fastmcp import FastMCP

from app.core.config import settings
from app.core.graph_query import code_metrics as _core_code_metrics
from app.core.graph_query import get_callees as _core_get_callees
from app.core.graph_query import get_callers as _core_get_callers
from app.core.graph_query import get_module_deps as _core_get_module_deps

mcp = FastMCP("graph-mcp", host=settings.mcp_host, port=settings.mcp_graph_port)

TIMEOUT = 5


async def _run(timeout: float, fn, *args) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
    except TimeoutError:
        return {"error": f"timeout after {timeout}s"}
    except Exception as e:  # noqa: BLE001 —— 永不炸穿 MCP 协议层
        return {"error": f"internal error: {type(e).__name__}: {e}"}


@mcp.tool()
async def get_callees(repo: str, class_name: str, method: str, depth: int = 2) -> dict:
    """下游调用链（callee 方向），递归 CTE。class_name 对应 spec §3.2 的 class 字段。depth 上限 5。"""
    return await _run(TIMEOUT, _core_get_callees, repo, class_name, method, min(depth, 5))


@mcp.tool()
async def get_callers(repo: str, class_name: str, method: str, depth: int = 2) -> dict:
    """上游调用链（caller 方向），递归 CTE。class_name 对应 spec §3.2 的 class 字段。depth 上限 5。"""
    return await _run(TIMEOUT, _core_get_callers, repo, class_name, method, min(depth, 5))


@mcp.tool()
async def get_module_deps(repo: str, module: str) -> dict:
    """模块间依赖（按 callee module 分组，top3 key classes）。"""
    return await _run(TIMEOUT, _core_get_module_deps, repo, module)


@mcp.tool()
async def code_metrics(repo: str, class_name: str, method_name: str | None = None) -> dict:
    """实体度量查询（圈复杂度、扇入扇出、LOC）。class_name 对应 spec §3.2 的 class 字段。"""
    return await _run(TIMEOUT, _core_code_metrics, repo, class_name, method_name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true", help="stdio 传输（默认 streamable-http）")
    args = parser.parse_args()
    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
