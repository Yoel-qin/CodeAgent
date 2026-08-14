"""QA / 幻觉 eval 编排（M39）。

对每条 query：走 legacy 生成（generate_fn DI，默认 retrieve→build_context→llm.chat）
得 answer+citations → M34 enforce 算 unverified_rate → LLMJudge 打 4 维 → 宏平均。
不走 run_eval（那是检索召回，QA 要走到生成）。rubric 参数化，M40 diagnosis 复用 judge。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.citation_enforcer import enforce
from app.clients.llm_client import llm
from app.eval.judge import QA_RUBRIC, LLMJudge
from app.retrieval.pipeline import pipeline
from app.retrieval.query_understanding import extract_query_terms
from app.services.chat_service import _citation, build_context

logger = logging.getLogger(__name__)

_QA_GEN_SYSTEM = (
    "你是代码知识库问答助手。基于下方检索到的代码/文档片段回答用户问题。"
    "回答须忠于片段，不要编造未出现的方法/字段/配置；引用用 chunk_id 或 类名.方法名。"
)


def _ctx_from(citations: list[dict]) -> str:
    return "\n".join(f"{c.get('chunk_id')}: {c.get('label')}" for c in citations[:10]) or "（无）"


@dataclass
class QAQuery:
    id: str
    text: str
    scoring_hints: dict = field(default_factory=dict)


@dataclass
class QAReport:
    config: dict
    aggregate: dict
    n_queries: int
    n_evaluable: int
    per_query: list[dict]
    unresolved: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


def load_qa_queries(path: str) -> list[QAQuery]:
    """从 yaml/json 加载 QA 集（字段 id/text/scoring_hints）。"""
    with open(path, encoding="utf-8") as f:
        if path.endswith((".yaml", ".yml")):
            import yaml

            data = yaml.safe_load(f)
        else:
            data = json.load(f)
    return [
        QAQuery(
            id=str(q["id"]),
            text=str(q["text"]),
            scoring_hints=dict(q.get("scoring_hints") or {}),
        )
        for q in data.get("queries", [])
    ]


async def _default_generate(
    session: AsyncSession, query: str, *, top_k: int, rewrite: str = "off"
):
    """默认 legacy 生成：recall → build_context → llm.chat（rewrite=off 确定性）。"""
    kw = (
        dict(semantic_query=query, terms=extract_query_terms(query), rewritten=False)
        if rewrite == "off"
        else {}
    )
    cands, meta = await pipeline.recall(session, query, top_k=top_k, **kw)
    context = build_context(cands)
    citations = [_citation(c) for c in cands[:top_k]]
    if not llm.configured:
        return ("", citations, meta)
    answer = await llm.chat(
        [
            {"role": "system", "content": _QA_GEN_SYSTEM + "\n\n=== 引用片段 ===\n" + context},
            {"role": "user", "content": query},
        ],
        temperature=0,
        max_tokens=1024,
    )
    return (answer, citations, meta)


def aggregate_qa(per_query: list[dict], rubric: dict) -> dict:
    """宏平均：5 维均值（4 judge + unverified_rate）+ weighted_quality（仅 rubric 4 维）。"""
    n = len(per_query)
    dims = list(rubric.keys()) + ["unverified_rate"]
    if n == 0:
        return {"n": 0, "means": {d: None for d in dims}, "weighted_quality": None}
    means: dict = {}
    for dim in rubric:
        vals = [
            pq["judge_scores"].get(dim)
            for pq in per_query
            if pq["judge_scores"].get(dim) is not None
        ]
        means[dim] = round(sum(vals) / len(vals), 4) if vals else None
    uv = [pq["unverified_rate"] for pq in per_query if pq["unverified_rate"] is not None]
    means["unverified_rate"] = round(sum(uv) / len(uv), 4) if uv else None
    num = den = 0.0
    for dim, cfg in rubric.items():
        m = means[dim]
        if m is None:
            continue
        v = m if cfg["direction"] == "high_good" else (1.0 - m)
        num += v * cfg["weight"]
        den += cfg["weight"]
    return {"n": n, "means": means, "weighted_quality": round(num / den, 4) if den else None}


async def run_qa_eval(
    session: AsyncSession,
    queries: Sequence[QAQuery],
    *,
    top_k: int = 8,
    rewrite: str = "off",
    generate_fn=None,
    rubric: dict = QA_RUBRIC,
    judge=None,
) -> QAReport:
    """逐 query：生成 → enforce(unverified_rate) → judge(4 维) → per_query；宏平均聚合。"""
    gen = generate_fn or _default_generate
    _judge = judge or LLMJudge()
    per_query: list[dict] = []
    unresolved: list[dict] = []

    for q in queries:
        try:
            answer, citations, meta = await gen(session, q.text, top_k=top_k, rewrite=rewrite)
        except Exception as e:  # 单 query 生成失败不中断
            logger.warning("qa eval query %s generate failed: %s", q.id, e)
            per_query.append(
                {
                    "id": q.id,
                    "text": q.text,
                    "answer": "",
                    "citations_n": 0,
                    "unverified_rate": None,
                    "judge_scores": {k: None for k in rubric},
                    "rationale": "",
                    "weighted_score": None,
                    "error": f"{type(e).__name__}: {e}",
                }
            )
            continue
        try:
            ce = enforce(answer, citations)
            unverified_rate = ce["ratio"]
        except Exception:
            unverified_rate = None
        try:
            jr = await _judge.judge(
                q.text, answer, _ctx_from(citations), citations, rubric=rubric,
                scoring_hints=q.scoring_hints,
            )
            scores = {k: v.score for k, v in jr.scores.items()}
            rationale = jr.rationale
            judge_error = None
        except Exception as e:  # DI judge 可能无内部容错，不中断整轮
            logger.warning("qa eval query %s judge failed: %s", q.id, e)
            scores = {k: None for k in rubric}
            rationale = ""
            judge_error = f"judge failed: {type(e).__name__}: {e}"
        per_query.append(
            {
                "id": q.id,
                "text": q.text,
                "answer": answer[:500],
                "citations_n": len(citations),
                "unverified_rate": unverified_rate,
                "judge_scores": scores,
                "rationale": rationale,
                "weighted_score": None,
                "error": judge_error,
            }
        )

    # 回填每条 weighted_score（单条版方向统一）
    for pq in per_query:
        num = den = 0.0
        for dim, cfg in rubric.items():
            s = pq["judge_scores"].get(dim)
            if s is None:
                continue
            v = s if cfg["direction"] == "high_good" else (1.0 - s)
            num += v * cfg["weight"]
            den += cfg["weight"]
        pq["weighted_score"] = round(num / den, 4) if den else None

    agg = aggregate_qa(per_query, rubric)
    return QAReport(
        config={"top_k": top_k, "rewrite": rewrite},
        aggregate=agg,
        n_queries=len(queries),
        n_evaluable=sum(1 for pq in per_query if not pq["error"]),
        per_query=per_query,
        unresolved=unresolved,
    )
