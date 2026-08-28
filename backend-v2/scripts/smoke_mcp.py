"""code-mcp 冒烟：MCP client 连通 → tools/list → grep 真实仓库。

用法：先起 server（python -m app.mcp_servers.code_server），再
uv run python scripts/smoke_mcp [--pattern PATTERN] [--repo rocketmq]

SERVER 侧 env/.env 控制 REPOS_ROOT/DEFAULT_REPO——client 无法覆盖。
如遇 "repo not found"，按错误提示重启 server 时设置正确的环境变量。
"""
import argparse
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402

from app.core.config import settings  # noqa: E402


async def main(pattern: str, repo: str) -> int:
    client = MultiServerMCPClient({
        "code": {"url": f"http://{settings.mcp_host}:{settings.mcp_code_port}/mcp", "transport": "streamable_http"}
    })
    tools = await client.get_tools()
    print(f"[smoke] tools/list: {[t.name for t in tools]}")
    grep = next(t for t in tools if t.name == "grep_code")
    res = await grep.ainvoke({"pattern": pattern, "repo": repo, "max_results": 5})
    print(f"[smoke] grep {pattern!r} @ {repo}:\n{res}")

    res_str = str(res)
    if "repo not found" in res_str:
        print("\n[smoke] 错误：server 侧仓库路径错误。请设置环境变量后重启 server：")
        print(f"  REPOS_ROOT=<repos_root_dir> DEFAULT_REPO={repo} uv run python -m app.mcp_servers.code_server")
        return 1
    return 0 if '"matches"' in res_str else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="MAX_RECONSUME_TIMES")
    parser.add_argument("--repo", default=None)
    args = parser.parse_args()
    repo = args.repo or settings.default_repo
    sys.exit(asyncio.run(main(args.pattern, repo)))
