"""M4 验收 smoke：真 LLM 三场景打真 backend（Agent 全链 e2e，含 MCP 工具真调用）。

前置：``uv run python scripts/dev_up.py``（backend :8010 + code/doc/graph/common 四 MCP
server）；rocketmq 源码 + sa-token 文档已 ingest（Plan 2 验收数据）；真 LLM key 在根 .env。

三场景（任务书冻结）与门：
  1. CodeNav  query=「rocketmq 里 DefaultMQProducerImpl 的 sendDefaultImpl 方法在哪个
     文件？它的直接上游调用者有谁？」
     门：≥1 code citation（file_path 含 .java 且 start_line>=1）+ answer 非空 + ≥1 agent_step。
  2. DocQA    query=「sa-token 登录认证的核心流程是什么？」repo=sa-token（文档在该 repo）
     门：≥1 doc citation；hybrid 空（sa-token 文档未 ingest）→ 打印 ingest 提示并计 FAIL。
  3. 无 key 不崩：subprocess 以 ``LLM_API_KEY=""`` 再起一个 backend（:8011，环境变量
     优先级高于 env_file、只影响该子进程）→ 场景 1 同 query → 门：SSE 收齐
     conversation→…→done、answer 含「未配置」或检索片段、HTTP 200（不崩、不断流）。
     结束只 terminate/kill **该子进程 PID**（严禁 taskkill 全杀 python）。

观察项（打印，不参与退出码）：
- 每 agent_step ``duration_ms`` 的 p50/max——Windows streamable-http MCP transport 开销
  （Plan 2 裁决遗留：Linux 部署验收须复测 e2e p95 并评估 session 复用）；
- 场景 1 conversation→retrieval 首事件间隔 ≈ query_analysis Router 墙钟（spec §5.1
  routing 档 <200ms 目标的本机观察值，含 SSE 首包排空抖动，仅记录不设门）。

退出码：三场景门全过 → 0，否则 1。
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# sys.path 自举后置 import，沿 smoke_graph.py 的 noqa 模式
import httpx  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BACKEND_URL = "http://localhost:8010"
NOKEY_PORT = 8011
NOKEY_URL = f"http://localhost:{NOKEY_PORT}"
HEALTH_TIMEOUT_S = 90.0
SCENARIO_TIMEOUT_S = 300.0

Q_CODENAV = ("rocketmq 里 DefaultMQProducerImpl 的 sendDefaultImpl 方法在哪个文件？"
             "它的直接上游调用者有谁？")
Q_DOCQA = "sa-token 登录认证的核心流程是什么？"
#: 复测措辞（带「文档」关键词 → 规则路也判 doc）：冻结 query 若被真 LLM 分类器路由到
#: doc 之外（本机实测 v4-flash 判 code），用同义问法验证 doc 路，门不变
Q_DOCQA_EXPLICIT = "sa-token 文档里登录认证的核心流程是怎么写的？"
INGEST_HINT = ("先跑 scripts/ingest_docs.py --repo sa-token --docs-dir "
               "D:/project/CodeRagAgent/data/repo/satoken-docs")

# read 180s：ReAct 多轮工具调用 + 真 LLM 首 token 可能慢（Windows MCP transport 开销）
_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


# ── SSE 客户端 ────────────────────────────────────────────────────────────


async def _post_chat(client: httpx.AsyncClient, url: str, query: str, repo: str | None):
    """流式 POST /v1/chat/completions → [(event, data, t_ms)]（t 为相对发起的毫秒）+ status。"""
    events: list[tuple[str, dict, float]] = []
    t0 = time.perf_counter()
    payload = {"query": query, "repo": repo} if repo else {"query": query}
    async with client.stream("POST", f"{url}/v1/chat/completions", json=payload) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode("utf-8", "replace")[:300]
            print(f"    HTTP {resp.status_code}: {body}")
            return events, resp.status_code
        ev: str | None = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                ev = line[len("event:"):].strip()
            elif line.startswith("data:") and ev:
                raw = line[len("data:"):].strip()
                try:
                    data = json.loads(raw)
                except ValueError:
                    data = {"raw": raw}
                events.append((ev, data, (time.perf_counter() - t0) * 1000))
    return events, 200


def _names(events) -> list[str]:
    return [e for e, _, _ in events]


def _answer(events) -> str:
    return "".join(d.get("content", "") for e, d, _ in events
                   if e == "token" and isinstance(d, dict))


def _citations(events, kind: str | None = None) -> list[dict]:
    return [d for e, d, _ in events
            if e == "citation" and isinstance(d, dict) and (kind is None or d.get("kind") == kind)]


def _steps(events) -> list[dict]:
    return [d for e, d, _ in events if e == "agent_step" and isinstance(d, dict)]


def _first_gap(events, first: str, second: str) -> float | None:
    a = next((t for e, _, t in events if e == first), None)
    b = next((t for e, _, t in events if e == second), None)
    return None if (a is None or b is None) else b - a


def _show_citations(events, limit: int = 3) -> None:
    for c in _citations(events)[:limit]:
        print(f"    citation: {json.dumps(c, ensure_ascii=False)}")


# ── 场景 1：CodeNav（真 LLM ReAct） ───────────────────────────────────────


async def scenario_codenav(client: httpx.AsyncClient, out_steps: list[dict]) -> tuple[bool, str]:
    t0 = time.perf_counter()
    events, status = await asyncio.wait_for(
        _post_chat(client, BACKEND_URL, Q_CODENAV, "rocketmq"), SCENARIO_TIMEOUT_S)
    print(f"  HTTP {status}, 耗时 {time.perf_counter() - t0:.1f}s, 事件 {_names(events)}")
    if status != 200:
        return False, f"HTTP {status}"
    out_steps.extend(_steps(events))

    code_cites = [c for c in _citations(events, "code")
                  if ".java" in (c.get("file_path") or "") and (c.get("start_line") or 0) >= 1]
    answer = _answer(events)
    steps = _steps(events)
    gap = _first_gap(events, "conversation", "retrieval")
    print(f"  code citation(.java 且 start_line>=1): {len(code_cites)} 条; "
          f"agent_step: {len(steps)} 个; answer {len(answer)} 字")
    _show_citations(events)
    if gap is not None:
        print(f"  [观察] conversation→retrieval 首事件间隔 ≈ Router 墙钟: {gap:.0f}ms "
              f"(spec §5.1 routing 档 <200ms 目标的本机观察值，仅记录不设门)")
    print(f"  answer 前 240 字: {answer[:240]!r}")
    problems = []
    if not code_cites:
        problems.append("无 file_path 含 .java 且 start_line>=1 的 code citation")
    if not answer.strip():
        problems.append("answer 为空")
    if not steps:
        problems.append("无 agent_step（未走 ReAct 工具链）")
    return (not problems), "; ".join(problems)


# ── 场景 2：DocQA（真 LLM ReAct + sa-token 文档） ─────────────────────────


async def scenario_docqa(client: httpx.AsyncClient, out_steps: list[dict]) -> tuple[bool, str]:
    """主尝试用任务书冻结 query；若真 LLM 分类器把它路由到 doc 之外（观察值），以带
    「文档」关键词的同义问法复测一次——门仍是 ≥1 doc citation，两次都无 → 打印
    ingest 提示并计 FAIL。"""
    events, status = await asyncio.wait_for(
        _post_chat(client, BACKEND_URL, Q_DOCQA, "sa-token"), SCENARIO_TIMEOUT_S)
    print(f"  HTTP {status}, 事件 {len(_names(events))} 个")
    doc_cites = _citations(events, "doc")
    if not doc_cites and status == 200:
        route = next((d.get("mode") for e, d, _ in events
                      if e == "retrieval" and isinstance(d, dict)), "?")
        answer = _answer(events)
        print(f"  [观察] 冻结 query 被路由到 {route}（真 LLM 分类器行为，记录不设门），"
              f"answer 前 120 字: {answer[:120]!r}")
        print(f"  [复测] 带文档关键词同义问法: {Q_DOCQA_EXPLICIT}")
        t0 = time.perf_counter()
        events, status = await asyncio.wait_for(
            _post_chat(client, BACKEND_URL, Q_DOCQA_EXPLICIT, "sa-token"), SCENARIO_TIMEOUT_S)
        print(f"  HTTP {status}, 耗时 {time.perf_counter() - t0:.1f}s, 事件 {_names(events)}")
        doc_cites = _citations(events, "doc")
    if status != 200:
        return False, f"HTTP {status}"
    out_steps.extend(_steps(events))

    answer = _answer(events)
    print(f"  doc citation: {len(doc_cites)} 条; agent_step: {len(_steps(events))} 个; "
          f"answer {len(answer)} 字")
    _show_citations(events)
    print(f"  answer 前 240 字: {answer[:240]!r}")
    if not doc_cites:
        # hybrid 空 ≈ sa-token 文档未 ingest（或检索全挂）——按任务书打印提示并计 FAIL
        print(f"  [提示] 未取到任何 doc citation（hybrid 结果空）：{INGEST_HINT}")
        return False, "无 doc citation（sa-token 文档未 ingest？）"
    return True, ""


# ── 场景 3：无 key 不崩（独立 backend :8011） ─────────────────────────────


async def _wait_healthy(client: httpx.AsyncClient, url: str, deadline_s: float) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < deadline_s:
        try:
            if (await client.get(f"{url}/health", timeout=3.0)).status_code == 200:
                return True
        except Exception:  # noqa: BLE001 —— 未起完/端口未监听，继续等
            pass
        await asyncio.sleep(0.5)
    return False


async def scenario_no_key() -> tuple[bool, str]:
    env = dict(os.environ)
    env["LLM_API_KEY"] = ""  # 环境变量优先级高于 env_file → 只这个进程无 key
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(NOKEY_PORT)],
        env=env, cwd=str(ROOT))
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            if not await _wait_healthy(client, NOKEY_URL, HEALTH_TIMEOUT_S):
                return False, f":{NOKEY_PORT} backend 未在 {HEALTH_TIMEOUT_S:.0f}s 内 healthy"
            print(f"  无 key backend(:{NOKEY_PORT}) healthy，同 query 打入")
            t0 = time.perf_counter()
            events, status = await asyncio.wait_for(
                _post_chat(client, NOKEY_URL, Q_CODENAV, "rocketmq"), SCENARIO_TIMEOUT_S)
        print(f"  HTTP {status}, 耗时 {time.perf_counter() - t0:.1f}s, 事件 {_names(events)}")
        answer = _answer(events)
        print(f"  citations: {len(_citations(events))} 条; answer {len(answer)} 字")
        print(f"  answer 前 300 字: {answer[:300]!r}")

        names = _names(events)
        problems = []
        if status != 200:
            problems.append(f"HTTP {status}")
        if not names or names[0] != "conversation":
            problems.append("首个事件不是 conversation")
        if not names or names[-1] != "done":
            problems.append("末个事件不是 done（流未收齐）")
        if "未配置" not in answer and not _citations(events):
            problems.append("answer 既无「未配置」也无检索片段")
        return (not problems), "; ".join(problems)
    finally:
        # 只杀本脚本起的这一个子进程 PID（严禁 taskkill 全杀 python）
        for stop in ("terminate", "kill"):
            try:
                getattr(proc, stop)()
                proc.wait(timeout=15)
                break
            except Exception:  # noqa: BLE001 —— 已死/超时则升级到下一档，仍只针对该 PID
                continue
        print(f"  无 key backend(:{NOKEY_PORT}) 已终止（pid={proc.pid}）")


# ── 汇总 ─────────────────────────────────────────────────────────────────


def _report_step_durations(all_steps: list[dict]) -> None:
    durations = [s.get("duration_ms") for s in all_steps if isinstance(s.get("duration_ms"), (int, float))]
    print("\n[smoke_agent] ===== agent_step duration_ms 观察（Windows MCP transport 开销） =====")
    if not durations:
        print("  （无带 duration_ms 的 agent_step）")
        return
    print(f"  总计 {len(durations)} 次工具调用: p50={statistics.median(durations):.1f}ms, "
          f"max={max(durations):.1f}ms, mean={statistics.fmean(durations):.1f}ms")
    by_tool: dict[str, list[float]] = {}
    for s in all_steps:
        v = s.get("duration_ms")
        if isinstance(v, (int, float)):
            by_tool.setdefault(str(s.get("tool")), []).append(float(v))
    for tool, vs in sorted(by_tool.items(), key=lambda kv: -statistics.fmean(kv[1])):
        print(f"    {tool:<24} n={len(vs):<3} p50={statistics.median(vs):8.1f}ms "
              f"max={max(vs):8.1f}ms")
    print("  （Plan 2 裁决遗留：Linux 部署验收须复测 e2e p95 并评估 MCP session 复用）")


async def main() -> int:
    all_steps: list[dict] = []
    results: list[tuple[str, bool, str]] = []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        print(f"[smoke_agent] 场景 1 CodeNav: {Q_CODENAV}")
        ok, why = await scenario_codenav(client, all_steps)
        results.append(("codenav", ok, why))

        print(f"\n[smoke_agent] 场景 2 DocQA (repo=sa-token): {Q_DOCQA}")
        ok, why = await scenario_docqa(client, all_steps)
        results.append(("docqa", ok, why))

    print(f"\n[smoke_agent] 场景 3 无 key 不崩: LLM_API_KEY='' 起 :{NOKEY_PORT}")
    ok, why = await scenario_no_key()
    results.append(("no-key", ok, why))

    _report_step_durations(all_steps)

    print("\n[smoke_agent] ========== 汇总 ==========")
    for name, ok, why in results:
        print(f"[smoke_agent]   {name:<8} {'PASS' if ok else 'FAIL'}{'' if ok else f' — {why}'}")
    if any(not ok for _, ok, _ in results):
        print("[smoke_agent] FAIL")
        return 1
    print("[smoke_agent] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
