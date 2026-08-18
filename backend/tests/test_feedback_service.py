"""M43 反馈闭环服务单测（无 DB：假 session + 真 ORM 实例）。"""
from __future__ import annotations

import app.services.feedback_service as svc
from app.db.models.chat import ChatMessage, Conversation
from app.db.models.eval import CandidateEvalQuery
from app.db.models.system import RetrievalLog
from app.schemas.conversation import FEEDBACK_CATEGORIES


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """仅覆盖 save_feedback 用到的面：get / execute(select) / add / commit。"""

    def __init__(self, *, msg, rlog, conv, existing_candidate=None):
        self._by_model = {ChatMessage: msg, RetrievalLog: rlog, Conversation: conv}
        self._existing = existing_candidate
        self.added: list = []
        self.committed = 0

    async def get(self, model, pk):
        return self._by_model.get(model)

    async def execute(self, stmt):
        return _FakeResult([self._existing] if self._existing else [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


def _msg(rlog_id=7):
    return ChatMessage(message_id="m1", conversation_id="c1", role="assistant",
                       content="答案", retrieval_log_id=rlog_id)


def _rlog():
    return RetrievalLog(log_id=7, query_text="RocketMQ 消息堆积怎么排查", recall_results={})


def conv():
    return Conversation(conversation_id="c1", title="t", target_repo="apache/rocketmq")


async def test_save_feedback_negative_with_category_persists_and_creates_candidate():
    s = _FakeSession(msg=_msg(), rlog=_rlog(), conv=conv())
    res = await svc.save_feedback(
        s, message_id="m1", rating="NOT_HELPFUL",
        categories=["答案错误"], correction=None)
    assert res == {"persisted": True, "candidate_created": True}
    assert s.committed == 2  # 反馈 commit + 候选 commit
    assert len(s.added) == 1
    cand = s.added[0]
    assert cand.query == "RocketMQ 消息堆积怎么排查"       # 取 rlog.query_text
    assert cand.categories == ["答案错误"]
    assert cand.repo == "apache/rocketmq"                    # 取 Conversation.target_repo
    assert cand.source_message_id == "m1"


async def test_save_feedback_correction_alone_creates_candidate():
    s = _FakeSession(msg=_msg(), rlog=_rlog(), conv=conv())
    res = await svc.save_feedback(s, message_id="m1", rating="NOT_HELPFUL",
                                  categories=["其他"], correction="应该是 DefaultMessageStore")
    assert res["candidate_created"] is True                  # 门槛：有纠错文本即入集


async def test_save_feedback_helpful_no_candidate():
    s = _FakeSession(msg=_msg(), rlog=_rlog(), conv=conv())
    res = await svc.save_feedback(s, message_id="m1", rating="HELPFUL",
                                  categories=None, correction=None)
    assert res == {"persisted": True, "candidate_created": False}
    assert s.added == []
    # 新列不写（HELPFUL 清掉旧负反馈的分类痕迹）
    rlog = s._by_model[RetrievalLog]
    assert rlog.feedback_categories is None


async def test_save_feedback_negative_other_only_no_candidate():
    s = _FakeSession(msg=_msg(), rlog=_rlog(), conv=conv())
    res = await svc.save_feedback(s, message_id="m1", rating="NOT_HELPFUL",
                                  categories=["其他"], correction=None)
    assert res["candidate_created"] is False                 # 门槛：答案错误/内容编造 或 纠错文本


async def test_save_feedback_idempotent_on_message():
    s = _FakeSession(msg=_msg(), rlog=_rlog(), conv=conv(),
                     existing_candidate=CandidateEvalQuery(
                         id=1, query="q", source_message_id="m1", status="CANDIDATE"))
    res = await svc.save_feedback(s, message_id="m1", rating="NOT_HELPFUL",
                                  categories=["答案错误"], correction=None)
    assert res["candidate_created"] is False                 # uk 幂等：已入集不重复
    assert s.added == []


async def test_save_feedback_no_rlog_not_persisted():
    s = _FakeSession(msg=_msg(rlog_id=None), rlog=None, conv=conv())
    res = await svc.save_feedback(s, message_id="m1", rating="NOT_HELPFUL",
                                  categories=["答案错误"], correction=None)
    assert res == {"persisted": False, "candidate_created": False}


class _IntegrityErrorFakeSession(_FakeSession):
    """commit 第二次调用时抛 IntegrityError（模拟 uk 并发冲突）。"""

    def __init__(self, *, msg, rlog, conv, existing_candidate=None):
        super().__init__(msg=msg, rlog=rlog, conv=conv, existing_candidate=existing_candidate)
        self._call = 0

    async def commit(self):
        self._call += 1
        if self._call == 2:  # 候选 INSERT 的 commit
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("duplicate key", {}, None)
        self.committed += 1

    async def rollback(self):
        pass


async def test_save_feedback_integrity_error_on_candidate_treated_as_existing():
    """uk 冲突（并发 re-submit）不报错，candidate_created=False。"""
    s = _IntegrityErrorFakeSession(msg=_msg(), rlog=_rlog(), conv=conv())
    res = await svc.save_feedback(
        s, message_id="m1", rating="NOT_HELPFUL",
        categories=["答案错误"], correction=None)
    assert res == {"persisted": True, "candidate_created": False}  # 反馈落盘，候选跳过
    assert s.committed == 1  # 仅反馈 commit 成功


def test_feedback_categories_enum():
    assert FEEDBACK_CATEGORIES == frozenset(
        {"答案错误", "引用不符", "检索遗漏", "答非所问", "内容编造", "其他"})
