"""Agent 面板聚合统计单测（无基础设施）：纯 helper + 假 session 编排查询结果→响应组装。

与 test_graph_service（纯 helper）/ test_document_api（_FakeSession）同风格：服务内多次
``session.execute`` 顺序固定，假 session 用「结果队列」按序弹出，确定性可测。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from app.api.v1.agents import router as agents_router
from app.services.agent_stats_service import (
    _DEGRADED,
    _ENGAGED,
    _HAS_TOOLS,
    _STEPS_LEN,
    _ratio,
    _since,
    get_agent_runs,
    get_agent_stats,
)

# ---- 纯 helper ----


def test_ratio_zero_denominator_is_none():
    assert _ratio(1, 0) is None  # 除零→None（面板「无数据」）


def test_ratio_normal():
    assert _ratio(1, 4) == 0.25


def test_ratio_zero_numerator():
    assert _ratio(0, 5) == 0.0


def test_since_all_returns_none():
    assert _since("all") is None


def test_since_today_is_utc_midnight():
    s = _since("today")
    assert s is not None and s.tzinfo is not None
    assert (s.hour, s.minute, s.second, s.microsecond) == (0, 0, 0, 0)


def test_since_7d_returns_past():
    s = _since("7d")
    assert s is not None and s.tzinfo is not None


# ---- 假 session（结果队列） ----


class _Result:
    def __init__(self, *, one=None, all=None, scalar_one=None):
        self._one, self._all, self._scalar = one, all, scalar_one

    def one(self):
        return self._one

    def all(self):
        return self._all

    def scalar_one(self):
        return self._scalar


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, *a, **k):
        return self._results.pop(0)


# ---- get_agent_stats：KPI + per_agent 组装 ----


async def test_get_agent_stats_composes_kpi_and_per_agent():
    # KPI：total_calls=2, engaged=3, degraded=1, helpful=1, feedback=2, avg_steps=4.0
    # per_agent：CODE_UNDERSTAND(2 调用/满意度0.5) + BUG_DIAGNOSIS(1 调用/1 降级)
    session = _FakeSession([
        _Result(one=(2, 3, 1, 1, 2, 4.0)),
        _Result(all=[
            ("CODE_UNDERSTAND", 2, 3.5, 0.5, 1, 2, 0),
            ("BUG_DIAGNOSIS", 1, 2.0, 1.0, 0, 0, 1),
        ]),
    ])
    resp = await get_agent_stats(session, "all")

    assert resp.window == "all"
    assert resp.total_calls == 2
    assert resp.engaged == 3
    assert resp.degraded == 1
    assert resp.degradation_rate == round(1 / 3, 4)  # 0.3333
    assert resp.avg_steps == 4.0
    assert resp.helpful == 1 and resp.feedback == 2
    assert resp.satisfaction == 0.5

    assert len(resp.per_agent) == 2
    cu = resp.per_agent[0]
    assert cu.agent == "CODE_UNDERSTAND" and cu.calls == 2
    assert cu.avg_steps == 3.5 and cu.hit_rate == 0.5
    assert cu.satisfaction == 0.5 and cu.degraded == 0
    bug = resp.per_agent[1]
    assert bug.agent == "BUG_DIAGNOSIS" and bug.degraded == 1
    assert bug.satisfaction is None  # 0/0 → None


async def test_get_agent_stats_empty_window_is_graceful():
    # 空窗口：所有聚合 None/0 → 比率 None、per_agent 空（不抛）
    session = _FakeSession([
        _Result(one=(None, None, None, None, 0, None)),
        _Result(all=[]),
    ])
    resp = await get_agent_stats(session, "today")
    assert resp.total_calls == 0 and resp.engaged == 0 and resp.degraded == 0
    assert resp.degradation_rate is None  # 0/0
    assert resp.satisfaction is None  # feedback=0
    assert resp.avg_steps is None
    assert resp.per_agent == []


# ---- get_agent_runs：分页 + 字段映射 + 截断 ----


async def test_get_agent_runs_maps_rows_and_total():
    dt = datetime(2026, 7, 29, tzinfo=UTC)
    session = _FakeSession([
        _Result(all=[(10, dt, "CODE_UNDERSTAND", "Foo.bar 做什么", 3, 2, False, "HELPFUL")]),
        _Result(scalar_one=5),
    ])
    resp = await get_agent_runs(session, {"page": 1, "page_size": 20, "offset": 0})
    assert resp.total == 5
    assert len(resp.items) == 1
    it = resp.items[0]
    assert it.log_id == 10 and it.agent == "CODE_UNDERSTAND"
    assert it.created_at == dt
    assert it.steps == 3 and it.citations == 2
    assert it.degraded is False and it.feedback == "HELPFUL"
    assert it.query == "Foo.bar 做什么"  # <60 不截断


async def test_get_agent_runs_truncates_long_query():
    long_q = "x" * 120
    dt = datetime(2026, 7, 29, tzinfo=UTC)
    session = _FakeSession([
        _Result(all=[(11, dt, None, long_q, 0, 0, True, None)]),  # 降级 run（agent 经回退仍 None→展示）
        _Result(scalar_one=1),
    ])
    resp = await get_agent_runs(session, {"page": 1, "page_size": 20, "offset": 0})
    it = resp.items[0]
    assert it.query == "x" * 60  # 截断到 60
    assert it.degraded is True and it.agent is None


# ---- 路由注册（无需 app/DB） ----


def test_agents_router_endpoints_registered():
    paths = {r.path for r in agents_router.routes}
    assert "/agents/stats" in paths
    assert "/agents/runs" in paths


# ---- M41 三形状谓词编译级接线测试（无需 PG，断言 SQL 片段存在即可）----
# 语义由静态 jsonpath 字面量保证；真实 DB 行为由集成测试覆盖。


def test_has_tools_predicate_covers_three_shapes():
    sql = str(_HAS_TOOLS.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "jsonb_typeof" in sql
    assert "jsonb_array_length" in sql
    assert '$.spans[*] ? (@.kind == "tool")' in sql  # dict 形状分支存在


def test_engaged_degraded_use_has_tools_not_isnot():
    eng = str(_ENGAGED.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    deg = str(_DEGRADED.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "agent_steps IS NOT NULL" not in eng  # 旧 `isnot(None)` 已换掉
    assert eng.count("jsonb_typeof") >= 1
    assert deg.count("jsonb_typeof") >= 1


def test_steps_len_compiles_three_shapes():
    sql = str(_STEPS_LEN.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "WHEN" in sql
    assert '$.spans[*] ? (@.kind == "tool")' in sql
