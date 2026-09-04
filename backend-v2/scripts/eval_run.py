"""M8 评测 CLI。

用法（backend-v2 目录下）：
    uv run python scripts/eval_run.py --validate                # 锚点校准（不跑批）
    uv run python scripts/eval_run.py                           # 单 baseline 跑批（落库）
    uv run python scripts/eval_run.py --judge                   # + QA 4 维 LLM 评判
    uv run python scripts/eval_run.py --ab r4:rounds_code=4 --ab nograph:code_no_graph=1
    uv run python scripts/eval_run.py --no-persist              # 预览（不落库）

退出码：--validate 有 case 零可解析锚点（code/doc 全 spec unresolved）→ 1；跑批
FAILED → 1。部分 spec unresolved 仍列印但不计 BAD——与 metrics ``has_*_anchor``
分母语义对称（双锚点容错：任一 spec 解析即可评分）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# sys.path 自举（允许 ``uv run python scripts/eval_run.py`` 直接跑；同 ingest_code.py 模式）
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

sys.stdout.reconfigure(encoding="utf-8")  # 中文 Windows GBK 控制台

from app.core.config import settings  # noqa: E402


def parse_variant_arg(raw: str) -> dict:
    """``name:rounds_code=4,code_no_graph=1`` → EvalVariant 字段 dict。

    值解析：``1/true`` → True、``0/false`` → False、整数 → int、其余原样字符串
    （如 model_reasoning）。缺名 → ArgumentTypeError。
    """
    name, _, rest = raw.partition(":")
    if not name.strip():
        raise argparse.ArgumentTypeError(f"变体缺名字: {raw!r}")
    out: dict = {"name": name.strip()}
    for pair in filter(None, rest.split(",")):
        k, _, v = pair.partition("=")
        if not k.strip():
            raise argparse.ArgumentTypeError(f"变体键为空: {pair!r}")
        lv = v.strip().lower()
        if lv in ("1", "true"):
            out[k.strip()] = True
        elif lv in ("0", "false"):
            out[k.strip()] = False
        elif v.lstrip("-").isdigit():
            out[k.strip()] = int(v)
        else:
            out[k.strip()] = v
    return out


async def _validate(repo: str | None, path: str) -> int:
    from app.db.base import SessionLocal
    from app.eval import golden
    from app.services import eval_service

    default_repo, cases = golden.load_golden_set(path)
    fixed = eval_service.fix_repos(cases, repo or default_repo or settings.default_repo)
    async with SessionLocal() as session:
        anchors = await eval_service.resolve_anchors(session, fixed)
    bad = 0
    for c in fixed:
        unresolved = [s for s, ts in anchors[c.id]["code"].items() if not ts] + \
                     [s for s, ts in anchors[c.id]["doc"].items() if not ts]
        # 双锚点容错（评审校准同轮）：BAD 只数「零可解析锚点」的 case（与 metrics
        # has_*_anchor 分母语义对称）；残缺 spec 仍列印（校准线索）不碍退出码
        dead = not any(anchors[c.id]["code"].values()) and not any(anchors[c.id]["doc"].values())
        print(f"[{'BAD' if dead else 'OK '}] {c.id}  repo={c.repo}  "
              f"unresolved={unresolved if unresolved else '无'}")
        bad += dead
    print(f"共 {len(fixed)} case，零可解析锚点 {bad} 条" + ("（exit 1）" if bad else ""))
    return 1 if bad else 0


async def _run(args: argparse.Namespace) -> int:
    from app.services import eval_service

    variants = [parse_variant_arg(a) for a in (args.ab or [])] or None
    result = await eval_service.run_and_persist(
        repo=args.repo, variants=variants, judge=args.judge,
        golden_path=args.set, trigger="cli", persist=not args.no_persist)
    print(f"run_id={result['id']} status={result['status']} kind={result['kind']} "
          f"repo={result['repo']}")
    for name, agg in (result.get("metrics") or {}).get("variants", {}).items():
        print(f"  [{name}] " + " ".join(
            f"{k}={v}" for k, v in agg.items() if v is not None))
    if (result.get("metrics") or {}).get("judge"):
        print(f"  [judge] {result['metrics']['judge']}")
    if result["error"]:
        print(f"  error: {result['error']}")
    return 1 if result["status"] == "FAILED" else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="V2-M8 评测 CLI")
    ap.add_argument("--validate", action="store_true", help="只做锚点校准（不跑批）")
    ap.add_argument("--repo", default=None, help="覆盖 golden 顶层 repo")
    ap.add_argument("--set", dest="set", default=None, help="golden set 路径（默认 settings）")
    ap.add_argument("--judge", action="store_true", help="开启 QA 4 维 LLM 评判")
    ap.add_argument("--ab", action="append", default=None, metavar="name:k=v,...",
                    help="追加一个 A/B 变体（可多次）")
    ap.add_argument("--no-persist", action="store_true", help="预览（不落 eval_runs）")
    args = ap.parse_args()
    if args.validate:
        return asyncio.run(_validate(args.repo, args.set or settings.eval_golden_path))
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
