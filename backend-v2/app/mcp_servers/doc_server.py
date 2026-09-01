"""doc-mcp server：5 个只读文档检索工具的 FastMCP 薄包装。

- 每工具统一超时 8s（spec §3.2），超时返回 error 不抛
- core 纯函数经 asyncio.to_thread（**位置参数**——to_thread 不支持 kwargs）+ wait_for
- REPOS_ROOT / DEFAULT_REPO 环境变量可覆盖（测试注入 fixture）
- 运行：python -m app.mcp_servers.doc_server            → streamable-http :8111/mcp
        python -m app.mcp_servers.doc_server --stdio    → stdio（测试/同机 sidecar）
"""
import argparse
import asyncio
import sys

from mcp.server.fastmcp import FastMCP

from app.core.config import settings
from app.core.doc_search import (
    get_doc_toc as _core_get_doc_toc,
)
from app.core.doc_search import (
    hybrid_search as _core_hybrid_search,
)
from app.core.doc_search import (
    keyword_search as _core_keyword_search,
)
from app.core.doc_search import (
    read_doc_section as _core_read_doc_section,
)
from app.core.doc_search import (
    semantic_search as _core_semantic_search,
)

mcp = FastMCP("doc-mcp", host=settings.mcp_host, port=settings.mcp_doc_port)

TIMEOUT = 8
MAX_TOP_K = 10


async def _run(timeout: float, fn, *args) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
    except TimeoutError:
        return {"error": f"timeout after {timeout}s"}
    except Exception as e:  # noqa: BLE001 —— 永不炸穿 MCP 协议层
        return {"error": f"internal error: {type(e).__name__}: {e}"}


@mcp.tool()
async def doc_semantic_search(query: str, repo: str = "", top_k: int = 8,
                               module: str = "") -> dict:
    """向量语义检索文档段落。module 可选过滤模块。仅只读。"""
    tk = min(top_k, MAX_TOP_K)
    mod = module or None
    return await _run(TIMEOUT, _core_semantic_search, repo or settings.default_repo, query, tk, mod)


@mcp.tool()
async def doc_keyword_search(query: str, repo: str = "", top_k: int = 8) -> dict:
    """BM25 关键词检索文档段落。仅只读。"""
    tk = min(top_k, MAX_TOP_K)
    return await _run(TIMEOUT, _core_keyword_search, repo or settings.default_repo, query, tk)


@mcp.tool()
async def doc_hybrid_search(query: str, repo: str = "", top_k: int = 8,
                             module: str = "") -> dict:
    """混合检索（向量+BM25 RRF 融合），推荐使用。仅只读。"""
    tk = min(top_k, MAX_TOP_K)
    mod = module or None
    return await _run(TIMEOUT, _core_hybrid_search, repo or settings.default_repo, query, tk, mod)


@mcp.tool()
async def read_doc_section(doc_id: int, anchor: str, repo: str = "") -> dict:
    """读取指定文档段落的完整内容。仅只读。"""
    return await _run(TIMEOUT, _core_read_doc_section, repo or settings.default_repo, doc_id, anchor)


@mcp.tool()
async def get_doc_toc(doc_id: int | None = None, repo: str = "") -> dict:
    """获取文档目录结构。不传 doc_id 返回全仓库文档树。仅只读。"""
    return await _run(TIMEOUT, _core_get_doc_toc, repo or settings.default_repo, doc_id)


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
