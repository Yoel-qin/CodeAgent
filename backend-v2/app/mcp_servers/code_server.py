"""code-mcp server：4 个只读代码检索工具的 FastMCP 薄包装。

- 每工具独立超时（spec §3.3：grep 10s / read 5s），超时返回 error 不抛
- core 纯函数经 asyncio.to_thread（**位置参数**——to_thread 不支持 kwargs）+ wait_for
- REPOS_ROOT / DEFAULT_REPO 环境变量可覆盖（测试注入 fixture）
- 运行：python -m app.mcp_servers.code_server            → streamable-http :8110/mcp
        python -m app.mcp_servers.code_server --stdio    → stdio（测试/同机 sidecar）
"""
import argparse
import asyncio
import sys

from mcp.server.mcpserver import MCPServer

from app.core.config import settings
from app.core.fs_guard import PathEscapeError
from app.core.grep import grep_code as _core_grep_code
from app.core.reader import list_directory as _core_list_directory
from app.core.reader import read_file as _core_read_file
from app.core.symbols import find_symbol as _core_find_symbol

mcp = MCPServer("code-mcp")

GREP_TIMEOUT = 10
READ_TIMEOUT = 5


async def _run(timeout: float, fn, *args) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
    except TimeoutError:
        return {"error": f"timeout after {timeout}s"}
    except PathEscapeError as e:
        return {"error": f"path outside repos root: {e}"}


@mcp.tool()
async def grep_code(pattern: str, file_glob: str = "*.java", case_sensitive: bool = True,
                    max_results: int = 20, repo: str = "") -> dict:
    """按正则/关键词搜索源码。file_glob 如 '**/broker/**/*.java'；max_results 上限 100。仅只读。"""
    return await _run(GREP_TIMEOUT, _grep_code_impl, pattern, file_glob, case_sensitive, min(max_results, 100), repo or settings.default_repo)


def _grep_code_impl(pattern, file_glob, case_sensitive, max_results, repo):
    return _core_grep_code(settings.repos_root, repo, pattern, file_glob, case_sensitive, max_results)


@mcp.tool()
async def read_file(file_path: str, start_line: int | None = None, end_line: int | None = None,
                    repo: str = "") -> dict:
    """读取文件指定行范围（默认前 500 行）。file_path 相对仓库根，如 'broker/src/main/java/X.java'。"""
    return await _run(READ_TIMEOUT, _core_read_file, settings.repos_root, repo or settings.default_repo, file_path, start_line, end_line)


@mcp.tool()
async def list_directory(path: str = "", depth: int = 2, repo: str = "") -> dict:
    """浏览仓库目录结构，depth 上限 3。path 相对仓库根，空 = 仓库根。"""
    return await _run(READ_TIMEOUT, _core_list_directory, settings.repos_root, repo or settings.default_repo, path, min(depth, 3))


@mcp.tool()
async def find_symbol(symbol_name: str, ref_type: str = "def", repo: str = "") -> dict:
    """符号定义/引用查找。ref_type='def'（定义）或 'ref'（引用）。"""
    return await _run(GREP_TIMEOUT, _core_find_symbol, settings.repos_root, repo or settings.default_repo, symbol_name, ref_type)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true", help="stdio 传输（默认 streamable-http）")
    args = parser.parse_args()
    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host=settings.mcp_host, port=settings.mcp_code_port)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
