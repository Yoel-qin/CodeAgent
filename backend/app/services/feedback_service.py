"""M43 反馈闭环服务：分类/纠错落库 + 候选 eval 集入集门槛 + 聚类报告。

写入侧 ``save_feedback`` 一个事务内做三件事：更新 retrieval_logs（旧列照旧 + M43 新列）、
按门槛 INSERT candidate_eval_queries（uk source_message_id 幂等：先查后插，重复反馈不重复入集）、
commit。报告侧 ``build_feedback_report`` 见 Task 4。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat import ChatMessage, Conversation
from app.db.models.eval import CandidateEvalQuery
from app.db.models.system import RetrievalLog
from app.retrieval.query_understanding import extract_query_terms

# 入集门槛分类：直接指向「答案有问题」的两类（检索遗漏等属召回问题，进报告即可）
_CANDIDATE_CATEGORIES = frozenset({"答案错误", "内容编造"})


def _should_create_candidate(rating: str, categories: list[str] | None,
                             correction: str | None) -> bool:
    """NOT_HELPFUL 且（分类含 答案错误/内容编造，或 填了纠错文本）。"""
    if rating != "NOT_HELPFUL":
        return False
    if correction:
        return True
    return bool(categories and _CANDIDATE_CATEGORIES & set(categories))


async def save_feedback(
    session: AsyncSession, *, message_id: str, rating: str,
    categories: list[str] | None, correction: str | None,
) -> dict:
    """写反馈 + 达门槛入候选集。返回 {persisted, candidate_created}。

    message 不存在时抛 KeyError 由端点转 404；无关联 retrieval_log 时 persisted=False
    （沿旧端点语义：静默不落库，也不入集）。
    """
    msg = await session.get(ChatMessage, message_id)
    if msg is None:
        raise KeyError(message_id)
    persisted = False
    candidate_created = False
    if msg.retrieval_log_id:
        rlog = await session.get(RetrievalLog, msg.retrieval_log_id)
        if rlog is not None:
            rlog.user_feedback = rating
            rlog.feedback_time = datetime.now(UTC)
            # 新列：仅负反馈写分类/纠错；HELPFUL 覆盖时清空（避免残留旧痕迹）
            rlog.feedback_categories = list(categories) if (rating == "NOT_HELPFUL" and categories) else None
            rlog.feedback_correction = correction if rating == "NOT_HELPFUL" else None
            persisted = True
            if _should_create_candidate(rating, categories, correction):
                existing = (await session.execute(
                    select(CandidateEvalQuery).where(
                        CandidateEvalQuery.source_message_id == message_id)
                )).scalars().first()
                if existing is None:  # uk 幂等：先查后插
                    conv = await session.get(Conversation, msg.conversation_id)
                    session.add(CandidateEvalQuery(
                        query=rlog.query_text,
                        categories=list(categories) if categories else None,
                        correction=correction,
                        source_message_id=message_id,
                        retrieval_log_id=rlog.log_id,
                        repo=conv.target_repo if conv else None,
                        status="CANDIDATE",
                    ))
                    candidate_created = True
    await session.commit()
    return {"persisted": persisted, "candidate_created": candidate_created}


# ---- 聚类报告（M43：零 API key，jieba 已有） ----


@dataclass
class _ReportRow:
    """报告聚合的扁平行（select 显式列，避免整行 ORM 开销）。"""
    log_id: int
    query_text: str
    feedback_categories: list | None
    feedback_correction: str | None
    recall_results: dict
    repo: str | None


async def build_feedback_report(session: AsyncSession, days: int = 30) -> dict:
    """五段式反馈报告：summary / categories / by_repo / keywords / hallucination_alerts。

    窗口内负反馈行一次取回、Python 侧聚合（反馈量小；SQL 侧 jsonb 展开不必要）。
    主查询失败 → 全段空态（monitor 惯例：永不 500）。
    """
    since = datetime.now(UTC) - timedelta(days=days)
    try:
        rows = (await session.execute(
            select(
                RetrievalLog.log_id, RetrievalLog.query_text,
                RetrievalLog.feedback_categories, RetrievalLog.feedback_correction,
                RetrievalLog.recall_results, Conversation.target_repo,
            )
            .join(ChatMessage, ChatMessage.retrieval_log_id == RetrievalLog.log_id)
            .join(Conversation, Conversation.conversation_id == ChatMessage.conversation_id)
            .where(RetrievalLog.user_feedback == "NOT_HELPFUL",
                   RetrievalLog.feedback_time >= since)
        )).all()
    except Exception:  # noqa: BLE001  主查询失败 → 空态
        rows = []

    total_neg = len(rows)
    cat_counter: Counter[str] = Counter()
    repo_counter: Counter[str] = Counter()
    word_counter: Counter[str] = Counter()
    alerts: list[dict] = []
    for r in rows:
        cats = list(r.feedback_categories or [])
        cat_counter.update(cats)
        repo_counter.update([r.repo or "未知"])
        try:
            word_counter.update(extract_query_terms(r.query_text))
        except Exception:  # noqa: BLE001  切词失败不影响其余段
            pass
        if "内容编造" in cats:
            enforcement = (r.recall_results or {}).get("enforcement") or {}
            alerts.append({
                "log_id": r.log_id,
                "query": r.query_text,
                "correction": (r.feedback_correction or "")[:200],
                "enforcement_ratio": enforcement.get("ratio"),   # M34 真键 ratio；关/缺 → None
                "repo": r.repo,
            })

    keywords = [w for w, _ in word_counter.most_common(20) if len(w) > 1][:20]
    return {
        "summary": {"total": total_neg, "negative": total_neg, "negative_rate": 1.0 if total_neg else 0.0},
        "categories": [{"category": c, "count": n} for c, n in
                       sorted(cat_counter.items(), key=lambda kv: -kv[1])],
        "by_repo": [{"repo": rp, "count": n} for rp, n in
                    sorted(repo_counter.items(), key=lambda kv: -kv[1])],
        "keywords": keywords,
        "hallucination_alerts": alerts,
    }
