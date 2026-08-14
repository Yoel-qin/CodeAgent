"""诊断 eval 编排(M40):对系统生成的诊断答案按 4 维 rubric LLMJudge 打分。

镜像 M39 qa_service 的 run_qa_eval,关键差异:
- rubric 权重逐 query(eval_set_diag.yaml 每条可覆盖 rubric_weights_default);
- expected 三元组(root_cause_hints/relevant_code/config_suggestions)作为 scoring_hints
  锚点注入 judge(设计决策①:LLM 语义判断,非文本匹配);
- 不跑 M34 enforce(诊断 rubric 无幻觉维,unverified_rate 不适用);
- overall = per-query weighted_score 的宏平均(每 query 权重不同,不能用全局权重加权 means)。
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.eval.judge import LLMJudge
from app.eval.qa_service import default_generate

logger = logging.getLogger(__name__)

# 4 维定义(desc 进 judge prompt;direction 全 high_good)。权重由 eval_set_diag.yaml 逐 query 提供。
DIAG_DIMS: dict = {
    "root_cause": {"desc": "答案是否点出正确的故障根因/瓶颈(对照根因提示)。1=准确点出,0=方向错误。", "direction": "high_good"},
    "code_ref": {"desc": "答案是否引用与问题相关的真实代码组件(类/服务,对照相关代码清单)。1=引用准确相关,0=无引用或编造。", "direction": "high_good"},
    "config_advice": {"desc": "答案给出的配置/参数建议是否合理可操作(对照配置建议清单)。1=合理具体,0=无建议或错误。", "direction": "high_good"},
    "reasoning": {"desc": "排查/分析推理是否连贯有逻辑(现象→假设→验证)。1=清晰连贯,0=混乱跳跃。", "direction": "high_good"},
}


@dataclass
class DiagQuery:
    id: str
    text: str
    intent: str
    expected: dict = field(default_factory=dict)  # {root_cause_hints, relevant_code, config_suggestions}
    rubric: dict = field(default_factory=dict)    # {dim: weight}


@dataclass
class DiagReport:
    config: dict
    aggregate: dict
    n_queries: int
    n_evaluable: int
    per_query: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


def load_diag_queries(path: str) -> list[DiagQuery]:
    """从 yaml 加载诊断集;每 query rubric 缺维度时用 rubric_weights_default 补齐。"""
    with open(path, encoding="utf-8") as f:
        import yaml

        data = yaml.safe_load(f)
    default_w: dict = data.get("rubric_weights_default") or {}
    return [
        DiagQuery(
            id=str(q["id"]),
            text=str(q["text"]),
            intent=str(q.get("intent", "diagnose")),
            expected=dict(q.get("expected") or {}),
            rubric={**default_w, **(q.get("rubric") or {})},
        )
        for q in data.get("queries", [])
    ]


def build_rubric(weights: dict) -> dict:
    """{dim: weight} → judge rubric({dim: {desc, direction, weight}})。

    未知维度忽略;0 权重保留(judge 照打分保数据完整,加权时贡献为 0)。
    """
    return {dim: {**DIAG_DIMS[dim], "weight": float(w)} for dim, w in weights.items() if dim in DIAG_DIMS}


def _weighted(judge_scores: dict, rubric: dict) -> float | None:
    """按该 query 自己的 rubric 权重加权(4 维全 high_good,无方向翻转);全 None → None。"""
    num = den = 0.0
    for dim, cfg in rubric.items():
        s = judge_scores.get(dim)
        if s is None:
            continue
        num += s * cfg["weight"]
        den += cfg["weight"]
    return round(num / den, 4) if den else None


def aggregate_diag(per_query: list[dict]) -> dict:
    """宏平均:4 维对非 None judge_scores 均值 + overall = per-query weighted_score 均值。"""
    means: dict = {}
    for dim in DIAG_DIMS:
        vals = [
            pq["judge_scores"].get(dim)
            for pq in per_query
            if (pq.get("judge_scores") or {}).get(dim) is not None
        ]
        means[dim] = round(sum(vals) / len(vals), 4) if vals else None
    ws = [pq["weighted_score"] for pq in per_query if pq.get("weighted_score") is not None]
    overall = round(sum(ws) / len(ws), 4) if ws else None
    return {"n": len(per_query), "means": means, "overall": overall}


async def run_diag_eval(
    session: AsyncSession,
    queries: Sequence[DiagQuery],
    *,
    top_k: int = 8,
    rewrite: str = "off",
    generate_fn=None,
    judge=None,
) -> DiagReport:
    """逐 query:generate → judge(该 query rubric + expected 锚点)→ per_query;宏平均聚合。

    generate / judge 异常各自 try/except 隔离(per_query 标 error,不中断整轮)——M39 同款契约。
    """
    gen = generate_fn or default_generate
    _judge = judge or LLMJudge()
    per_query: list[dict] = []

    for q in queries:
        rubric = build_rubric(q.rubric)
        try:
            answer, citations, _meta = await gen(session, q.text, top_k=top_k, rewrite=rewrite)
        except Exception as e:  # 单 query 生成失败不中断
            logger.warning("diag eval query %s generate failed: %s", q.id, e)
            per_query.append(
                {
                    "id": q.id, "text": q.text, "intent": q.intent, "answer": "",
                    "citations_n": 0,
                    "judge_scores": {k: None for k in rubric},
                    "rationale": "", "weighted_score": None,
                    "error": f"{type(e).__name__}: {e}",
                }
            )
            continue
        try:
            jr = await _judge.judge(
                q.text, answer,
                "\n".join(f"{c.get('chunk_id')}: {c.get('label')}" for c in citations[:10]) or "(无)",
                citations, rubric=rubric, scoring_hints=q.expected,
            )
            scores = {k: v.score for k, v in jr.scores.items()}
            rationale = jr.rationale
            judge_error = None
        except Exception as e:  # DI judge 可能无内部容错,不中断整轮
            logger.warning("diag eval query %s judge failed: %s", q.id, e)
            scores = {k: None for k in rubric}
            rationale = ""
            judge_error = f"judge failed: {type(e).__name__}: {e}"
        per_query.append(
            {
                "id": q.id, "text": q.text, "intent": q.intent,
                "answer": answer[:500],
                "citations_n": len(citations),
                "judge_scores": scores,
                "rationale": rationale,
                "weighted_score": _weighted(scores, rubric),
                "error": judge_error,
            }
        )

    return DiagReport(
        config={"top_k": top_k, "rewrite": rewrite},
        aggregate=aggregate_diag(per_query),
        n_queries=len(queries),
        n_evaluable=sum(1 for pq in per_query if not pq["error"]),
        per_query=per_query,
    )
