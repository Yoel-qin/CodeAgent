"""检索 A/B 评测 CLI（横切·评测 / 后端设计 Phase 9 M24 / 收尾 M29）：兑现 §2/§3/§4「需评测集」delta。

用 ``app.services.eval_run_service.run_ab_and_persist`` 跑若干检索变体（经 ``AblationConfig``
关闭某环节），对照 on/off 的 Recall@K / Precision@K / NDCG / MRR delta。默认 3 组：
  - rerank       精排 on/off（no_rerank → full）          看 precision/NDCG/MRR
  - multipath_rrf 多路+RRF on/off（vector_only → full）   看 recall
  - graph        图遍历 on/off（no_graph → full）         看 recall

用法（从 backend/ 运行）::

  # 默认 --rewrite off（确定性），top-k 10，跑全部 3 组，结果落 eval_runs（trigger=cli）
  uv run python scripts/ab_eval.py --top-k 10

  # 不落库（ephemeral，仅 stdout/文件）
  uv run python scripts/ab_eval.py --no-persist

  # 仅跑精排组 + dump JSON
  uv run python scripts/ab_eval.py --pairs rerank --json

  # 图遍历组额外在「调用链子集」上跑（需 eval_set 标 tags: [call_chain]）
  uv run python scripts/ab_eval.py --graph-subset

前置：评测集（backend/eval/eval_set.yaml）引用的样本仓库须先入库（见 eval_set.yaml 的
target_repos）。FULL 变体的 rerank_on_count=0 时精排 delta 无意义，脚本会告警。

实现：薄封装 ``run_ab_and_persist``（M29 起持久化为 ``eval_runs`` 行 ``config.kind="ab"``、
``trigger="cli"``，与 ``EvalPage`` A/B Tab 历史统一）；async 会话经 ``app.db.AsyncSessionLocal``。
``run_ab_and_persist`` 返回 ``EvalRun``，其 ``config["report"]`` 即 ``ABReport.to_dict()`` 形状。
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
from app.eval.ab_service import DEFAULT_PAIRS  # noqa: E402
from app.services import eval_run_service  # noqa: E402

# (queries loaded inside run_ab_and_persist; tags passed through)

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EVAL_SET = os.path.join(_BACKEND_ROOT, "eval", "eval_set.yaml")
_KS = (1, 3, 5, 10)
_GRAPH_SUBSET_TAG = "call_chain"
_PAIR_BY_NAME = {p.name: p for p in DEFAULT_PAIRS}


def _fmt_val(v) -> str:
    return f"{v:.4f}" if isinstance(v, (int, float)) else "-"


def _fmt_pct(d: dict) -> str:
    p = d.get("pct")
    return f"{p:+.2f}%" if p is not None else "-"


def _print_variants(report: dict) -> None:
    print("\n[变体]  rerank_on / 可评")
    for name, v in report["variants"].items():
        print(f"  {name:<14}{v['rerank_on_count']}/{v['n_evaluable']}   ({v['desc']})")


def _print_pair(pair: dict, variants: dict) -> None:
    print(f"\n[{pair['name']}] {pair['claim']}   ({pair['baseline']} → {pair['treatment']})")
    b_agg = variants[pair["baseline"]]["aggregate"]
    t_agg = variants[pair["treatment"]]["aggregate"]
    delta = pair["delta"]
    for metric in pair["metric_focus"]:
        if metric == "mrr":
            print(f"  mrr          {_fmt_val(b_agg['mrr'])} → {_fmt_val(t_agg['mrr'])}   "
                  f"{_fmt_pct(delta['mrr'])}")
        else:
            for k in _KS:
                print(f"  {metric}@{k:<2}      {_fmt_val(b_agg[metric][k])} → "
                      f"{_fmt_val(t_agg[metric][k])}   {_fmt_pct(delta[metric][k])}")


def _print_report(report: dict, strategy: str, title: str = "检索 A/B 评测") -> None:
    cfg = report["config"]
    print(f"\n=== {title} ===")
    print(f"评测集 query: {cfg['n_queries']}   strategy={strategy}   "
          f"top_k={cfg['top_k']}   rewrite={cfg['rewrite']}")
    _print_variants(report)
    if report["variants"].get("full", {}).get("rerank_on_count", 0) == 0:
        print("\n⚠ FULL 未触发精排（reranker key 缺/未就绪）→ 精排组 delta 无意义（no_rerank ≡ full）。")
    for pair in report["pairs"]:
        _print_pair(pair, report["variants"])


def _print_vector_diagnosis(report: dict) -> None:
    """M25 诊断：对 vector_only 变体逐 query 定位向量路漏召模式。

    每行：relevant 是否进向量路（+ 1-based rank）、向量路返回的 kind 分布（code/doc 混比）、
    最终 retrieved 前 3 的 kind。直接印证「dual 向量路对中文 NL 返回 doc 漏 code」。
    仅当本次跑了 vector_only 变体（--pairs 含 multipath_rrf）时输出。

    注：``report`` 为 ``ABReport.to_dict()`` 形状的 dict（来自 ``EvalRun.config["report"]``）。
    """
    v = report["variants"].get("vector_only")
    if not v or not v.get("per_query"):
        return
    print("\n=== 向量路诊断（vector_only）===")
    print("  定位向量路漏召：relevant 是否进向量路 + 向量路返回的 kind 分布 + 最终 retrieved 前3 kind")
    for q in v["per_query"]:
        relevant = set(q.get("relevant") or [])
        vpath = (q.get("recall_paths") or {}).get("vector") or []
        v_ids = [c.get("chunk_id") for c in vpath]
        kind_dist: dict = {}
        for c in vpath:
            k = c.get("kind")
            kind_dist[k] = kind_dist.get(k, 0) + 1
        hit_rank = next((i for i, cid in enumerate(v_ids, 1) if cid in relevant), None)
        hit_str = f"是 rank={hit_rank}" if hit_rank else "否"
        dist = " ".join(f"{k}:{n}" for k, n in sorted(kind_dist.items(), key=lambda kv: -kv[1])) or "(空)"
        final_kinds = ",".join(str(k) for k in (q.get("retrieved_kinds") or [])[:3]) or "-"
        print(f"  {q.get('id', ''):<6} 入向量路={hit_str:<12} 向量路[{dist:<14}] 最终前3[{final_kinds}]")


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="检索 A/B 评测：on/off 各环节的 Recall@K/NDCG delta")
    ap.add_argument("--eval-set", default=DEFAULT_EVAL_SET, help="评测集路径（.yaml/.yml/.json）")
    ap.add_argument("--top-k", type=int, default=10, help="召回候选数（默认 10）")
    ap.add_argument("--rewrite", choices=["off", "auto"], default="off",
                    help="off=绕过 LLM 改写（确定性，默认）；auto=生产全链路")
    ap.add_argument("--pairs", nargs="+", default=list(_PAIR_BY_NAME),
                    choices=list(_PAIR_BY_NAME),
                    help="跑哪些 A/B 组（默认全部：rerank multipath_rrf graph crosslink）")
    ap.add_argument("--graph-subset", action="store_true",
                    help=f"图遍历组额外在「{_GRAPH_SUBSET_TAG}」tag 子集上跑")
    ap.add_argument("--tags", default=None,
                    help="按 tags 过滤评测集（逗号分隔，如 rocketmq 或 a,b；M31）")
    ap.add_argument("--diagnose", action="store_true",
                    help="打印向量路逐 query 诊断（relevant 是否入向量路 + kind 分布；需含 multipath_rrf）")
    ap.add_argument("--out", default=None, help="完整 JSON 报告输出路径")
    ap.add_argument("--json", action="store_true", help="打印完整 JSON 报告到 stdout")
    ap.add_argument("--no-persist", action="store_true",
                    help="不落 eval_runs（ephemeral，仅 stdout/文件）；默认持久化为 kind=ab/trigger=cli 行")
    args = ap.parse_args(argv)

    tags = None
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    run = None
    try:
        async with AsyncSessionLocal() as session:
            run = await eval_run_service.run_ab_and_persist(
                session, top_k=args.top_k, rewrite=args.rewrite, eval_set=args.eval_set,
                pairs=args.pairs, graph_subset=args.graph_subset, diagnose=args.diagnose,
                trigger="cli", persist=not args.no_persist, tags=tags,
            )
    finally:
        await engine.dispose()

    # run.config["report"] = ABReport.to_dict() 形状（含可选 graph_subset 子报告）
    report = (run.config or {}).get("report") or {"config": {}, "variants": {}, "pairs": []}
    _print_report(report, settings.embedding_strategy)
    if not args.no_persist and run.run_id:
        print(f"\n✓ 已持久化 eval_runs.run_id={run.run_id}（kind=ab, trigger=cli）")

    if args.diagnose:
        _print_vector_diagnosis(report)

    graph_subset_report = report.get("graph_subset")
    if graph_subset_report is not None:
        _print_report(graph_subset_report, settings.embedding_strategy,
                      title=f"图遍历 A/B · {_GRAPH_SUBSET_TAG} 子集")

    payload = report
    payload["config"]["embedding_strategy"] = settings.embedding_strategy
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
