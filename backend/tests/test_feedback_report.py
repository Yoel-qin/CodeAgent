"""M43 反馈聚类报告单测（无 DB：假 session 返 canned 行，验证 Python 侧聚合）。"""
from __future__ import annotations

import app.services.feedback_service as svc


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows
        self._scalars = _FakeScalars(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return self._scalars


class _FakeSession:
    def __init__(self, rlogs):
        self._rlogs = rlogs

    async def execute(self, stmt):
        return _FakeResult(self._rlogs)


def _rlog(log_id, q, cats, corr, enforcement_ratio=None, repo=None):
    recall = {"enforcement": {"enabled": True, "ratio": enforcement_ratio}} \
        if enforcement_ratio is not None else {}
    return svc._ReportRow(  # dataclass 行（实现细节，见 Step 3）
        log_id=log_id, query_text=q, feedback_categories=cats,
        feedback_correction=corr, recall_results=recall, repo=repo)


async def test_report_segments_and_category_distribution():
    rows = [
        _rlog(1, "RocketMQ 消息堆积怎么排查", ["答案错误", "引用不符"], None, 0.5, "apache/rocketmq"),
        _rlog(2, "怎么获取账户余额", ["内容编造"], "编造了 API", 0.9, None),
        _rlog(3, "账户取款逻辑", ["答案错误"], None, None, "apache/rocketmq"),
    ]
    rep = await svc.build_feedback_report(_FakeSession(rows), days=30)
    assert rep["summary"] == {"total": 3, "negative": 3, "negative_rate": 1.0}
    dist = {c["category"]: c["count"] for c in rep["categories"]}
    assert dist == {"答案错误": 2, "引用不符": 1, "内容编造": 1}
    assert rep["by_repo"] == [{"repo": "apache/rocketmq", "count": 2}, {"repo": "未知", "count": 1}]
    assert rep["keywords"] and isinstance(rep["keywords"], list)
    # 幻觉段：仅 内容编造 行，交叉 enforcement.ratio（缺失→None）
    alerts = rep["hallucination_alerts"]
    assert len(alerts) == 1 and alerts[0]["log_id"] == 2
    assert alerts[0]["enforcement_ratio"] == 0.9


async def test_report_empty():
    rep = await svc.build_feedback_report(_FakeSession([]), days=30)
    assert rep["summary"]["total"] == 0
    assert rep["categories"] == [] and rep["hallucination_alerts"] == []


async def test_report_segment_failure_degrades(monkeypatch):
    """任一段失败 → 该段降级，不 500（monitor 惯例）。"""
    class _BadSession:
        async def execute(self, stmt):
            raise RuntimeError("db")

    rep = await svc.build_feedback_report(_BadSession(), days=30)
    assert rep["summary"]["total"] == 0        # 主查询失败 → 全段空态而非异常
