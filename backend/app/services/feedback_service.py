"""M43 反馈闭环服务：分类/纠错落库 + 候选 eval 集入集门槛 + 聚类报告。

写入侧 ``save_feedback`` 一个事务内做三件事：更新 retrieval_logs（旧列照旧 + M43 新列）、
按门槛 INSERT candidate_eval_queries（uk source_message_id 幂等：先查后插，重复反馈不重复入集）、
commit。报告侧 ``build_feedback_report`` 见 Task 4。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat import ChatMessage, Conversation
from app.db.models.eval import CandidateEvalQuery
from app.db.models.system import RetrievalLog

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
