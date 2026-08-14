"""baseline 快照对比(M40):repo 内 baseline_diag.json 卡诊断 eval 指标退化。

纯函数 + 显式路径参数(CLI/CI 传入),不触碰全局状态。快照结构::

    {"metrics": {dim: float, "overall": float}, "meta": {"date": ..., "n_queries": ..., ...}}
"""
from __future__ import annotations

import json
from pathlib import Path


def load_baseline(path: str | Path) -> dict:
    """读 baseline 快照;文件不存在 → FileNotFoundError(调用方决定:CLI 提示先 --update-baseline)。"""
    with open(Path(path), encoding="utf-8") as f:
        return json.load(f)


def compare_baseline(current: dict, baseline: dict, *, threshold: float = 0.05) -> dict:
    """平面指标 dict 对比,指标集合取两边 key 并集。

    规则:任一边缺失/None → missing;``current[m] < baseline[m] - threshold``(严格)→ 退化。
    返回 ``{"ok": not regressions and not missing, "regressions": [...], "missing": [...]}``。
    """
    regressions: list[dict] = []
    missing: list[str] = []
    for m in sorted(set(current) | set(baseline)):
        c, b = current.get(m), baseline.get(m)
        if c is None or b is None:
            missing.append(m)
            continue
        if c < b - threshold:
            regressions.append({"metric": m, "current": c, "baseline": b, "delta": round(c - b, 4)})
    return {"ok": not regressions and not missing, "regressions": regressions, "missing": missing}


def write_baseline(metrics: dict, meta: dict, path: str | Path) -> None:
    """原子写快照(临时文件 + replace),ensure_ascii=False 保留中文。"""
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"metrics": metrics, "meta": meta}, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
