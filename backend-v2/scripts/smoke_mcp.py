"""code-mcp 冒烟：MCP client 连通 → tools/list → grep 真实仓库。

用法：先起 server（python -m app.mcp_servers.code_server），再
uv run python scripts/smoke_mcp [--pattern PATTERN] [--repo rocketmq]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from app.core.config import settings  # noqa: E402


async def main(pattern: str, repo: str) -> int:
    async with streamable_http_client(f"http://{settings.mcp_host}:{settings.mcp_code_port}/mcp") as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            # Initialize
            await session.initialize()

            # List tools
            tools_result = await session.list_tools()
            tools = tools_result.tools
            print(f"[smoke] tools/list: {[t.name for t in tools]}")

            # Call grep_code
            result = await session.call_tool("grep_code", {
                "pattern": pattern,
                "repo": repo,
                "max_results": 5
            })

            print(f"[smoke] grep {pattern!r} @ {repo}:")
            for content in result.content:
                if hasattr(content, 'text'):
                    print(content.text)
                else:
                    print(content)

            # Check if we got matches
            result_str = str(result)
            return 0 if "matches" in result_str or len(result.content) > 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default=None)
    parser.add_argument("--repo", default=None)
    args = parser.parse_args()
    repo = args.repo or settings.default_repo
    if args.pattern is None:
        has_real = (Path(settings.repos_root) / repo).is_dir()
        args.pattern = "MAX_RECONSUME_TIMES" if has_real else "MAX_RETRY_TIMES"
        if not has_real:
            repo = "mini_repo"
            print("[smoke] 真实仓库缺失，改搜 tests/fixtures/mini_repo")
    sys.exit(asyncio.run(main(args.pattern, repo)))
