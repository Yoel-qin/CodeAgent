"""诊断 eval CLI(M40):跑 eval_set_diag.yaml → 落 eval_runs(kind=diagnosis)→ baseline 退化门。

用法(从 backend/ 运行)::

  # 默认:跑 eval + 持久化 + 与 backend/eval/baseline_diag.json 对比(退化/缺失 → exit 1)
  uv run python scripts/diag_eval.py

  # 首次建立基线
  uv run python scripts/diag_eval.py --update-baseline

  # 不落库 / 只跑不比
  uv run python scripts/diag_eval.py --no-persist --skip-compare

前置:RocketMQ 源码已入库(module=rocketmq);LLM key 可用(生成 + 判官各一次调用/query)。
退出码:run FAILED / baseline 退化或缺失 → 1;首次无 baseline → 0(提示先建基线)。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import AsyncSessionLocal, engine  # noqa: E402
from app.eval.baseline import compare_baseline, load_baseline, write_baseline  # noqa: E402
from app.services import eval_run_service  # noqa: E402

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASELINE = os.path.join(_BACKEND_ROOT, "eval", "baseline_diag.json")
_DIMS = ("root_cause", "code_ref", "config_advice", "reasoning")


def _fmt(v) -> str:
    return f"{v:.4f}" if isinstance(v, (int, float)) else "-"


def _print_result(run) -> None:
    agg = run.aggregate or {}
    means = agg.get("means") or {}
    print("\n=== 诊断 eval 结果 ===")
    print(f"可评 query: {run.n_evaluable}/{run.n_queries}   top_k={run.top_k}  rewrite={run.rewrite}")
    for dim in _DIMS:
        print(f"  {dim:>14}: {_fmt(means.get(dim))}")
    print(f"  {'overall':>14}: {_fmt(agg.get('overall'))}")


def _current_metrics(run) -> dict:
    """DiagRun.aggregate → 平面指标 dict({各维: 分, overall: 分}),喂 compare_baseline。"""
    agg = run.aggregate or {}
    return {**(agg.get("means") or {}), "overall": agg.get("overall")}


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="诊断 eval:LLMJudge 4 维 + baseline 退化门")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--rewrite", choices=["off", "auto"], default="off")
    ap.add_argument("--eval-set", default=None, help="诊断评测集路径(默认 backend/eval/eval_set_diag.yaml)")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE, help="baseline 快照路径")
    ap.add_argument("--threshold", type=float, default=0.05, help="退化阈值(绝对分值,默认 0.05)")
    ap.add_argument("--update-baseline", action="store_true", help="用本次结果刷新 baseline 快照")
    ap.add_argument("--skip-compare", action="store_true", help="跳过 baseline 对比")
    ap.add_argument("--no-persist", action="store_true", help="不落 eval_runs")
    args = ap.parse_args(argv)

    async with AsyncSessionLocal() as session:
        run = await eval_run_service.run_diag_and_persist(
            session, top_k=args.top_k, rewrite=args.rewrite, eval_set=args.eval_set,
            trigger="cli", persist=not args.no_persist,
        )
    await engine.dispose()

    _print_result(run)
    if not args.no_persist and run.run_id:
        print(f"\n✓ 已持久化 eval_runs.run_id={run.run_id}(trigger=cli)")
    if run.status != "COMPLETED":
        print(f"\n✗ run FAILED: {run.error_message}")
        return 1

    metrics = _current_metrics(run)

    if args.update_baseline:
        write_baseline(
            metrics,
            {
                "date": datetime.now(UTC).isoformat(),
                "n_queries": run.n_queries,
                "top_k": run.top_k,
                "run_id": run.run_id,
            },
            args.baseline,
        )
        print(f"\n✓ baseline 已刷新: {args.baseline}")
        return 0

    if args.skip_compare:
        return 0

    try:
        snap = load_baseline(args.baseline)
    except FileNotFoundError:
        print(f"\n⚠ 无 baseline({args.baseline});首次运行请先 --update-baseline 建立基线")
        return 0

    result = compare_baseline(metrics, snap.get("metrics") or {}, threshold=args.threshold)
    if result["ok"]:
        print(f"\n✓ baseline 对比通过(threshold={args.threshold})")
        return 0
    for r in result["regressions"]:
        print(f"  ✗ 退化 {r['metric']}: {r['current']:.4f} < {r['baseline']:.4f}(Δ{r['delta']:+.4f})")
    for m in result["missing"]:
        print(f"  ✗ 缺失指标: {m}")
    print(f"\n✗ baseline 对比未通过(threshold={args.threshold})")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
