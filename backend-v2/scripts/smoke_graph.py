"""graph-mcp 冒烟：MCP client 连 :8112 → tools/list(断言 4 工具) → 调用图查询验收。

退出码：edges 非空且 p95 延迟 <100ms → 0，否则非 0。
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402

from app.core.config import settings  # noqa: E402

EXPECTED_TOOLS = 4
REPO = "rocketmq"
N_RUNS = 10  # 每工具调用次数，用于延迟分位
P95_THRESHOLD_MS = 100

# ── 内置锚查询（RocketMQ 4.9.8 已知实体） ─────────────────────────────
ANCHOR_QUERIES = [
    # (tool_name, args_dict, description)
    ("get_callers", {"repo": REPO, "class_name": "DefaultMQProducerImpl", "method": "sendDefaultImpl", "depth": 1},
     "DefaultMQProducerImpl.sendDefaultImpl 上游调用者"),
    ("get_callees", {"repo": REPO, "class_name": "DefaultMQPushConsumerImpl", "method": "start", "depth": 1},
     "DefaultMQPushConsumerImpl.start 下游调用链"),
    ("get_module_deps", {"repo": REPO, "module": "client"},
     "client 模块依赖"),
    ("code_metrics", {"repo": REPO, "class_name": "DefaultMQProducerImpl"},
     "DefaultMQProducerImpl 度量"),
]


def _unwrap(res: object) -> dict:
    """从 MCP content-block 列表或裸字符串/字典中提取 JSON dict。"""
    if isinstance(res, dict):
        return res
    if isinstance(res, list):
        for b in res:
            if isinstance(b, dict) and b.get("type") == "text" and "text" in b:
                inner = b["text"]
                if isinstance(inner, str):
                    try:
                        return json.loads(inner)
                    except (json.JSONDecodeError, ValueError):
                        pass
    text = str(res)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    return {"raw": text}


def _has_edges(result: dict) -> bool:
    """判断查询结果是否包含非空边数据。"""
    edges = result.get("edges")
    if isinstance(edges, list) and len(edges) > 0:
        return True
    deps = result.get("dependencies")
    if isinstance(deps, list) and len(deps) > 0:
        return True
    entities = result.get("entities")
    if isinstance(entities, list) and len(entities) > 0:
        return True
    return False


def _count_results(result: dict) -> int:
    """返回结果条目数（用于报告）。"""
    for key in ("edges", "dependencies", "entities"):
        v = result.get(key)
        if isinstance(v, list):
            return len(v)
    return 0


async def main() -> int:
    client = MultiServerMCPClient({
        "graph": {
            "url": f"http://{settings.mcp_host}:{settings.mcp_graph_port}/mcp",
            "transport": "streamable_http",
        }
    })

    # ── 1. tools/list ───────────────────────────────────────────────────
    tools = await client.get_tools()
    tool_names = [t.name for t in tools]
    print(f"[smoke_graph] tools/list ({len(tool_names)}): {tool_names}")
    if len(tool_names) < EXPECTED_TOOLS:
        print(f"[smoke_graph] FAIL: 期望 >= {EXPECTED_TOOLS} 工具，实际 {len(tool_names)}")
        return 1

    # ── 2. 逐锚查询 + 延迟采集 ─────────────────────────────────────────
    tool_map = {t.name: t for t in tools}

    # ── warmup: 1 throwaway call per tool to prime PG query plans ─────
    print("[smoke_graph] warmup calls...")
    for tool_name, args, _ in ANCHOR_QUERIES:
        fn = tool_map.get(tool_name)
        if fn:
            try:
                await fn.ainvoke(args)
            except Exception:  # noqa: BLE001
                pass

    all_latencies: list[float] = []  # ms
    all_edge_counts: list[int] = []

    for tool_name, args, desc in ANCHOR_QUERIES:
        tool_fn = tool_map.get(tool_name)
        if tool_fn is None:
            print(f"[smoke_graph] WARN: 工具 {tool_name!r} 不存在，跳过")
            continue

        print(f"\n[smoke_graph] 锚查询: {desc}")
        print(f"[smoke_graph]   args: {json.dumps(args, ensure_ascii=False)}")
        latencies: list[float] = []
        last_result: dict = {}
        best_count = 0

        for run_i in range(N_RUNS):
            t0 = time.perf_counter()
            try:
                res = await tool_fn.ainvoke(args)
            except Exception as exc:  # noqa: BLE001
                print(f"[smoke_graph]   run {run_i+1}: 异常 {exc}")
                continue
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            parsed = _unwrap(res)
            last_result = parsed
            cnt = _count_results(parsed)
            best_count = max(best_count, cnt)

        if not latencies:
            print(f"[smoke_graph]   全部 {N_RUNS} 次调用失败")
            continue

        all_latencies.extend(latencies)
        p50 = statistics.median(latencies)
        p95 = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 2 else latencies[-1]
        print(f"[smoke_graph]   runs={len(latencies)}, p50={p50:.1f}ms, p95={p95:.1f}ms, max={max(latencies):.1f}ms")

        # 打印结果摘要
        if "error" in last_result:
            print(f"[smoke_graph]   结果 error: {last_result['error']}")
        elif "edges" in last_result:
            total = last_result.get("total", len(last_result["edges"]))
            print(f"[smoke_graph]   edges: {len(last_result['edges'])} 条 (total={total}, truncated={last_result.get('truncated', False)})")
            # 抽检：打印前 3 条边
            for edge in last_result["edges"][:3]:
                if isinstance(edge, dict):
                    caller = f"{edge.get('caller_class','?')}.{edge.get('caller_method','?')}"
                    callee = f"{edge.get('callee_class','?')}.{edge.get('callee_method','?')}"
                    print(f"[smoke_graph]     {caller} -> {callee} (depth={edge.get('depth','?')})")
        elif "dependencies" in last_result:
            print(f"[smoke_graph]   dependencies: {len(last_result['dependencies'])} 个模块")
        elif "entities" in last_result:
            print(f"[smoke_graph]   entities: {len(last_result['entities'])} 个")

        all_edge_counts.append(best_count)

    # ── 3. 判定 ──────────────────────────────────────────────────────────
    has_data = any(c > 0 for c in all_edge_counts)
    overall_p95 = sorted(all_latencies)[int(len(all_latencies) * 0.95)] if len(all_latencies) >= 2 else (all_latencies[0] if all_latencies else float("inf"))
    overall_p50 = statistics.median(all_latencies) if all_latencies else float("inf")

    print("\n[smoke_graph] ========== 汇总 ==========")
    print(f"[smoke_graph] 总延迟采样: {len(all_latencies)}")
    if all_latencies:
        print(f"[smoke_graph]   p50={overall_p50:.1f}ms, p95={overall_p95:.1f}ms, max={max(all_latencies):.1f}ms")
    print(f"[smoke_graph] 各锚最大结果数: {all_edge_counts}")
    print(f"[smoke_graph] has_data={has_data}, p95={overall_p95:.1f}ms (threshold={P95_THRESHOLD_MS}ms)")

    if has_data and overall_p95 < P95_THRESHOLD_MS:
        print("[smoke_graph] PASS")
        return 0
    else:
        reasons = []
        if not has_data:
            reasons.append("所有锚查询返回空结果")
        if overall_p95 >= P95_THRESHOLD_MS:
            reasons.append(f"p95={overall_p95:.1f}ms >= {P95_THRESHOLD_MS}ms")
        print(f"[smoke_graph] FAIL: {'; '.join(reasons)}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
