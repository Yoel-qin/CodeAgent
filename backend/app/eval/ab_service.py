"""检索 A/B 评测编排（横切·评测 / 后端设计 Phase 9 M24）。

兑现开发清单 §2/§3/§4「需评测集」验收：用 :func:`app.eval.eval_service.run_eval` 跑若干
**检索变体**（经 :class:`app.retrieval.ablation.AblationConfig` 关闭某环节），对照 on/off 的
Recall@K / Precision@K / NDCG / MRR delta，给「双编码器 + 3 阶段重排 + RRF + 图遍历」一个
量化结论。

零运行时改动：变体经 ``run_eval`` 的 ``recall_fn`` DI 接缝注入（包装 :func:`pipeline.recall`
并传 ``ablation=…``）；生产链路 ``recall(ablation=None)`` 不受影响。默认 ``rewrite="off"``
绕过 Stage-0 LLM 改写，使 delta 可归因到漏斗环节本身（见 ``eval_service`` 模块 docstring）。
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.eval.eval_service import EvalQuery, EvalReport, run_eval
from app.retrieval.ablation import AblationConfig
from app.retrieval.pipeline import pipeline

logger = logging.getLogger(__name__)

_KS = (1, 3, 5, 10)
# delta 计算用到的指标键（recall/precision/ndcg 按 K，mrr 单值）
_METRIC_KEYS_K = ("recall", "precision", "ndcg")


@dataclass(frozen=True)
class ABVariant:
    """一个检索变体：名字 + 消融配置。"""

    name: str
    ablation: AblationConfig
    desc: str


# ---- 命名变体（FULL 三对共享 → run_ab 去重只跑一次）----
V_FULL = ABVariant("full", AblationConfig(), "全开（生产链路）")
V_NO_RERANK = ABVariant("no_rerank", AblationConfig(rerank=False), "关闭 Stage2/3 精排")
V_VECTOR_ONLY = ABVariant(
    "vector_only", AblationConfig(lexical=False, graph=False), "仅向量（Phase1 单路基线）"
)
V_NO_GRAPH = ABVariant("no_graph", AblationConfig(graph=False), "关闭图遍历召回")
V_NO_CROSSLINK = ABVariant(
    "no_crosslink", AblationConfig(crosslink=False), "关闭交叉链接第 5 路（M32）"
)

_VARIANTS: dict[str, ABVariant] = {v.name: v for v in (
    V_FULL, V_NO_RERANK, V_VECTOR_ONLY, V_NO_GRAPH, V_NO_CROSSLINK)}


@dataclass(frozen=True)
class ABPair:
    """一组 A/B 对照：baseline（不含该特性）→ treatment（全开），附验收声明量级。"""

    name: str
    claim: str
    baseline: str       # 变体名
    treatment: str      # 变体名
    metric_focus: tuple[str, ...]


# ---- 默认 4 组 A/B（对应 §2 精排 / §3 多路+RRF / §4 图遍历 / §11 交叉链接）----
DEFAULT_PAIRS: tuple[ABPair, ...] = (
    ABPair("rerank", "精排使精度 +15~25%", "no_rerank", "full", ("precision", "ndcg", "mrr")),
    ABPair("multipath_rrf", "多路+RRF 召回 +10~15%", "vector_only", "full", ("recall",)),
    ABPair("graph", "调用链召回 +20%+", "no_graph", "full", ("recall",)),
    ABPair("crosslink", "交叉链接第 5 路召回 +5~15%（M32）", "no_crosslink", "full", ("recall",)),
)


@dataclass
class ABReport:
    """A/B 评测报告：每变体聚合指标 + 每组 pair 的 delta。"""

    config: dict
    variants: dict        # 变体名 → {ablation, aggregate, n_evaluable, rerank_on_count, unresolved}
    pairs: list[dict]     # 每 pair 的 baseline/treatment aggregate + delta

    def to_dict(self) -> dict:
        return asdict(self)


def filter_by_tag(queries: Sequence[EvalQuery], tag: str) -> list[EvalQuery]:
    """筛出带某 tag 的 query（如 ``call_chain`` 子集）。"""
    return [q for q in queries if tag in q.tags]


def _make_recall_fn(ablation: AblationConfig):
    """构造注入 ``run_eval`` 的 recall 包装器：固定 ``ablation``、其余 kw 透传。"""

    async def _recall(session: AsyncSession, query: str, *, top_k: int, **kw):
        return await pipeline.recall(session, query, top_k=top_k, ablation=ablation, **kw)

    _recall.ablation = ablation  # 自描述：报告/测试可经此识别变体所用的消融配置
    return _recall


def _delta(bv: float | None, tv: float | None) -> dict:
    """单值 delta：abs = t−b；pct = (t−b)/b*100（b 为 None/0 → pct=None）。"""
    if bv is None or tv is None:
        return {"abs": None, "pct": None}
    abs_d = round(tv - bv, 4)
    pct = round((tv - bv) / bv * 100, 2) if bv != 0 else None
    return {"abs": abs_d, "pct": pct}


def _pair_deltas(baseline_agg: dict, treatment_agg: dict) -> dict:
    """对 recall/precision/ndcg（按 K）+ mrr 算 baseline→treatment delta。"""
    out: dict = {}
    for metric in _METRIC_KEYS_K:
        out[metric] = {k: _delta(baseline_agg.get(metric, {}).get(k),
                                 treatment_agg.get(metric, {}).get(k)) for k in _KS}
    out["mrr"] = _delta(baseline_agg.get("mrr"), treatment_agg.get("mrr"))
    return out


async def run_ab(
    session: AsyncSession,
    queries: Sequence[EvalQuery],
    *,
    top_k: int = 10,
    rewrite: str = "off",
    pairs: Sequence[ABPair] = DEFAULT_PAIRS,
) -> ABReport:
    """对每组 pair 的 baseline/treatment 变体各跑一遍评测，算 delta。

    - 同一变体（如 ``full`` 被多 pair 引用）**去重只跑一次**。
    - 每变体经 ``run_eval(recall_fn=_make_recall_fn(ablation))`` 跑真实检索管线。
    - 返回 :class:`ABReport`（``to_dict`` 可 JSON 序列化）。
    """
    used = set()
    for p in pairs:
        used.add(p.baseline)
        used.add(p.treatment)

    variants_out: dict = {}
    for vname in sorted(used):
        v = _VARIANTS[vname]
        rep: EvalReport = await run_eval(
            session, queries, top_k=top_k, rewrite=rewrite,
            recall_fn=_make_recall_fn(v.ablation),
        )
        variants_out[vname] = {
            "ablation": asdict(v.ablation),
            "desc": v.desc,
            "aggregate": rep.aggregate,
            "n_evaluable": rep.n_evaluable,
            "n_queries": rep.n_queries,
            "rerank_on_count": rep.rerank_on_count,
            "unresolved": len(rep.unresolved),
            # M25 诊断：透传逐 query 记录（含 recall_paths/retrieved_kinds），供 CLI --diagnose
            # 定位「向量路漏召/返回 kind」（默认只看 aggregate，per_query 此前被丢弃）。全 JSON-native。
            "per_query": rep.per_query,
        }

    pair_results: list[dict] = []
    for p in pairs:
        b_agg = variants_out[p.baseline]["aggregate"]
        t_agg = variants_out[p.treatment]["aggregate"]
        pair_results.append({
            "name": p.name,
            "claim": p.claim,
            "baseline": p.baseline,
            "treatment": p.treatment,
            "metric_focus": list(p.metric_focus),
            "delta": _pair_deltas(b_agg, t_agg),
        })

    return ABReport(
        config={"top_k": top_k, "rewrite": rewrite, "n_queries": len(queries)},
        variants=variants_out,
        pairs=pair_results,
    )
