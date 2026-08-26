"""检索评测 CLI（横切·评测 / 后端设计 Phase 9.2 / 收尾 M29）：跑真实检索管线 + Recall@K / MRR / NDCG。

用法（从 backend/ 运行）::

  # 仅校验评测集锚/类/chunk_id 命中率（不调检索，快速核对评测集与入库一致性）
  uv run python scripts/eval_retrieval.py --validate

  # 默认 --rewrite off（确定性，绕过 Stage-0 LLM 改写），top-k 10，结果落 eval_runs（trigger=cli）
  uv run python scripts/eval_retrieval.py --top-k 10

  # 不落库（ephemeral，仅 stdout/文件），恢复 M27 前行为
  uv run python scripts/eval_retrieval.py --no-persist

  # 生产全链路（含 LLM 改写，需 key、非确定）+ 打印完整 JSON
  uv run python scripts/eval_retrieval.py --rewrite auto --json

  # dump 完整报告（含逐 query 明细）到文件
  uv run python scripts/eval_retrieval.py --out eval_report.json

  # 按 tags 子集评测（M31 RocketMQ 中文子集）+ 消融单路（词法单路：关 vector/graph）
  uv run python scripts/eval_retrieval.py --tags rocketmq --ablation '{"vector":false,"graph":false}'

前置：评测集（backend/eval/eval_set.yaml）引用的样本仓库须先入库
（见 eval_set.yaml 的 target_repos）。未解析的 query 会被警告并跳过。

实现：薄封装 ``app.services.eval_run_service.run_and_persist``（M29 起持久化为 ``eval_runs`` 行，
``trigger="cli"``，CLI 与 ``EvalPage`` 历史统一）；async 会话经 ``app.db.AsyncSessionLocal``。
``--validate`` 路径不经 service（只解析标注，不调检索）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.db import AsyncSessionLocal, engine  # noqa: E402
from app.eval.eval_service import EvalQuery, resolve_relevant  # noqa: E402
from app.services import eval_run_service  # noqa: E402

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EVAL_SET = os.path.join(_BACKEND_ROOT, "eval", "eval_set.yaml")
_KS = (1, 3, 5, 10)


def _load_eval_set(path: str) -> tuple[dict, list[EvalQuery]]:
    with open(path, encoding="utf-8") as f:
        if path.endswith((".yaml", ".yml")):
            import yaml  # pyyaml 随 pydantic-settings/langchain 传递依赖；无则改用 .json
            data = yaml.safe_load(f)
        else:
            data = json.load(f)
    queries = [
        EvalQuery(id=str(q["id"]), text=str(q["text"]), relevant=list(q.get("relevant", [])))
        for q in data.get("queries", [])
    ]
    return data, queries


def _fmt(v: float | None) -> str:
    return f"{v:.4f}" if isinstance(v, (int, float)) else "-"


def _print_aggregate(run, strategy: str) -> None:
    agg = run.aggregate or {}
    print("\n=== 检索评测结果 ===")
    print(f"可评 query: {run.n_evaluable}/{run.n_queries}   "
          f"rerank_on: {run.rerank_on_count}/{run.n_evaluable}   "
          f"strategy={strategy}")
    print(f"top_k={run.top_k}  rewrite={run.rewrite}\n")
    # EvalRun.aggregate 的 K-key 经 JSONB 序列化为字符串（service._normalize_agg 已规整）
    print(f"{'K':>4} {'Recall':>9} {'Precision':>11} {'NDCG':>9}")
    for k in _KS:
        sk = str(k)
        print(f"{k:>4} {_fmt((agg.get('recall') or {}).get(sk)):>9} "
              f"{_fmt((agg.get('precision') or {}).get(sk)):>11} "
              f"{_fmt((agg.get('ndcg') or {}).get(sk)):>9}")
    print(f"\nMRR: {_fmt(agg.get('mrr'))}")
    if run.unresolved:
        print(f"\n⚠ 未解析 query（已跳过）: {len(run.unresolved)}")
        for u in run.unresolved:
            print(f"  - {u['id']}: {u['text']}  (missing: {u['missing']})")


async def _validate(session, queries) -> int:
    hit, total, unresolved = 0, 0, []
    for q in queries:
        total += len(q.relevant)
        resolved, missing = await resolve_relevant(session, q.relevant)
        hit += len(q.relevant) - len(missing)
        if not resolved:
            unresolved.append(q.id)
    rate = f"{hit / total * 100:.1f}%" if total else "无标注"
    print(f"\n锚/类/chunk_id 命中: {hit}/{total}  ({rate})")
    print(f"无法解析的 query: {len(unresolved)}  {unresolved}")
    return 0


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="检索评测：Recall@K / MRR / NDCG over 真实检索管线")
    ap.add_argument("--eval-set", default=DEFAULT_EVAL_SET, help="评测集路径（.yaml/.yml/.json）")
    ap.add_argument("--top-k", type=int, default=10, help="召回候选数（默认 10）")
    ap.add_argument("--rewrite", choices=["off", "auto"], default="off",
                    help="off=绕过 LLM 改写（确定性，默认）；auto=生产全链路")
    ap.add_argument("--out", default=None, help="完整 JSON 报告输出路径")
    ap.add_argument("--json", action="store_true", help="打印完整 JSON 报告到 stdout")
    ap.add_argument("--validate", action="store_true", help="仅校验评测集锚命中率，不调检索")
    ap.add_argument("--no-persist", action="store_true",
                    help="不落 eval_runs（ephemeral，仅 stdout/文件）；默认持久化为 trigger=cli 行")
    ap.add_argument("--tags", default=None,
                    help="按 tags 过滤评测集（逗号分隔，如 rocketmq；M31）")
    ap.add_argument("--ablation", default=None,
                    help="消融 JSON，如 '{\"vector\":false,\"graph\":false}'（M29 通道，M31 CLI 化）")
    args = ap.parse_args(argv)

    _, queries = _load_eval_set(args.eval_set)
    print(f"评测集: {args.eval_set}  ({len(queries)} queries)")

    tags = None
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    ablation = None
    if args.ablation:
        try:
            ablation = json.loads(args.ablation)
        except json.JSONDecodeError as e:
            ap.error(f"--ablation 非法 JSON: {e}")

    run = None
    try:
        async with AsyncSessionLocal() as session:
            if args.validate:
                return await _validate(session, queries)
            run = await eval_run_service.run_and_persist(
                session, top_k=args.top_k, rewrite=args.rewrite, eval_set=args.eval_set,
                trigger="cli", persist=not args.no_persist,
                tags=tags, ablation=ablation,
            )
    finally:
        await engine.dispose()

    _print_aggregate(run, settings.embedding_strategy)
    if not args.no_persist and run.run_id:
        print(f"\n✓ 已持久化 eval_runs.run_id={run.run_id}（trigger=cli）")

    # EvalRun 已含规整后的 aggregate/per_query/unresolved/config；组装成 report 形状供 dump
    payload = {
        "config": {**(run.config or {}), "embedding_strategy": settings.embedding_strategy},
        "aggregate": run.aggregate,
        "n_queries": run.n_queries,
        "n_evaluable": run.n_evaluable,
        "rerank_on_count": run.rerank_on_count,
        "per_query": run.per_query,
        "unresolved": run.unresolved,
    }
    if args.json:
        sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.flush()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n报告已写入: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
