"""doc-mcp 冒烟：MCP client 连 :8111 → tools/list(断言 5 工具) → doc_hybrid_search 命中验证。

退出码：返回含 "results" 且非空列表则 0，否则 1。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402

from app.core.config import settings  # noqa: E402

EXPECTED_TOOLS = 5
PRIMARY_QUERY = "登录 踰点 stpLogic"
FALLBACK_QUERY = "登录 时 使用 stpLogic"


def _unwrap(res: object) -> dict:
    """从 MCP content-block 列表或裸字符串/字典中提取 JSON dict。"""
    if isinstance(res, dict):
        return res
    # langchain-mcp-adapters 返回 [{"type":"text","text":"..."}] (Python list)
    if isinstance(res, list):
        for b in res:
            if isinstance(b, dict) and b.get("type") == "text" and "text" in b:
                inner = b["text"]
                if isinstance(inner, str):
                    try:
                        return json.loads(inner)
                    except (json.JSONDecodeError, ValueError):
                        pass
    # fallback: try str → JSON
    text = str(res)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    return {"raw": text}


async def _try_hybrid(client: MultiServerMCPClient, query: str, repo: str, top_k: int) -> dict:
    """调用 doc_hybrid_search 并返回解析后的结果 dict。"""
    tools = await client.get_tools()
    hybrid = next(t for t in tools if t.name == "doc_hybrid_search")
    res = await hybrid.ainvoke({"query": query, "repo": repo, "top_k": top_k})
    return _unwrap(res)


async def main() -> int:
    repo = "sa-token"
    top_k = 5
    client = MultiServerMCPClient({
        "doc": {
            "url": f"http://{settings.mcp_host}:{settings.mcp_doc_port}/mcp",
            "transport": "streamable_http",
        }
    })

    # --- tools/list ---
    tools = await client.get_tools()
    tool_names = [t.name for t in tools]
    print(f"[smoke_doc] tools/list ({len(tool_names)}): {tool_names}")
    if len(tool_names) < EXPECTED_TOOLS:
        print(f"[smoke_doc] FAIL: 期望 >= {EXPECTED_TOOLS} 工具，实际 {len(tool_names)}")
        return 1

    # --- hybrid search: 主查询（含历史锚点错别字「踰点」） ---
    for label, query in [("主查询", PRIMARY_QUERY), ("变体查询", FALLBACK_QUERY)]:
        print(f"\n[smoke_doc] {label}: {query!r}")
        try:
            result = await _try_hybrid(client, query, repo, top_k)
        except Exception as exc:  # noqa: BLE001
            print(f"[smoke_doc] {label} 调用异常: {exc}")
            continue

        print(f"[smoke_doc] 原始返回:\n{json.dumps(result, ensure_ascii=False, indent=2)[:3000]}")

        results = result.get("results")
        if isinstance(results, list) and len(results) > 0:
            print(f"[smoke_doc] PASS: {label} 命中 {len(results)} 条")
            return 0
        print(f"[smoke_doc] {label}: results 为空或非列表")

    # 两次查询均无命中
    print("[smoke_doc] FAIL: 两次查询均无命中")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
