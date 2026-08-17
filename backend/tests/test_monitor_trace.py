"""M41：/v1/monitor/traces 列表 + 详情（新 dict / 旧 list 伪 span / 404）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app

_DICT_PAYLOAD = {
    "version": 2,
    "spans": [
        {"span_id": 1, "parent_id": None, "kind": "request", "name": "chat",
         "start_ms": 0.0, "duration_ms": 100.0, "status": "ok", "error": None,
         "tokens": None, "attrs": {}},
        {"span_id": 2, "parent_id": 1, "kind": "tool", "name": "search_code",
         "start_ms": 10.0, "duration_ms": 30.0, "status": "ok", "error": None,
         "tokens": None, "attrs": {"args": {"query": "q"}, "n": 2}},
    ],
    "summary": {"total_ms": 100.0,
                "tokens": {"prompt": 10, "completion": 5, "n_llm_calls": 1,
                           "estimated": False},
                "n_spans": 2, "kind_counts": {"request": 1, "tool": 1}},
}


@pytest.fixture()
def fake_logs(monkeypatch):
    rows = [
        SimpleNamespace(log_id=1, query_text="dict 行", agent_steps=_DICT_PAYLOAD,
                       recall_results={"mode": "agent"}, total_latency_ms=None,
                       created_at=None),
        SimpleNamespace(log_id=2, query_text="旧 list 行",
                       agent_steps=[{"tool": "t", "args": {}, "n": 1}],
                       recall_results={"mode": "agent", "recall_ms": 8, "rerank_ms": 4},
                       total_latency_ms=12, created_at=None),
        SimpleNamespace(log_id=3, query_text="null 行", agent_steps=None,
                       recall_results={"mode": "default", "recall_ms": 6, "rerank_ms": 3},
                       total_latency_ms=9, created_at=None),
    ]

    class _Q:
        def __init__(self, session):
            self.session = session
        def filter(self, *a):
            return self
        def order_by(self, *a):
            return self
        def limit(self, n):
            return self
        def scalars(self):
            return self
        def all(self):
            return rows

    class _FakeSession:
        """假 session：execute 直接返回传入的 query 链对象（_Q），get 委托 fake_get。"""
        def __init__(self, fake_get_fn):
            self._fake_get = fake_get_fn
        async def execute(self, query):
            return query  # _Q 对象自带 scalars().all()
        async def get(self, *a, **k):
            return await self._fake_get(*a, **k)

    async def fake_get(session, log_id):
        return next((r for r in rows if r.log_id == log_id), None)

    fake_session = _FakeSession(fake_get)

    import app.services.monitor_service as ms
    monkeypatch.setattr(ms, "_select_logs", lambda session, since: _Q(session))
    monkeypatch.setattr(ms, "_get_log", fake_get)
    return rows, fake_session


@pytest.fixture(autouse=True)
def _override_db(fake_logs):
    """依赖覆盖：注入假 session（execute 返回 _Q 链，get 委托 fake_get）。"""
    _rows, fake_session = fake_logs
    async def _inject():
        return fake_session
    app.dependency_overrides[get_db] = _inject
    yield
    app.dependency_overrides.pop(get_db, None)


def test_traces_list_shapes(fake_logs):
    with TestClient(app) as client:
        resp = client.get("/v1/monitor/traces", params={"window": "all"}).json()
    assert resp["total"] == 3
    by_id = {i["log_id"]: i for i in resp["items"]}
    assert by_id[1]["has_trace"] is True
    assert by_id[1]["total_ms"] == 100.0
    assert by_id[1]["tokens"]["n_llm_calls"] == 1
    assert by_id[2]["has_trace"] is False and by_id[2]["total_ms"] == 12
    assert by_id[3]["has_trace"] is False


def test_trace_detail_dict(fake_logs):
    with TestClient(app) as client:
        d = client.get("/v1/monitor/traces/1").json()
    assert d["legacy"] is False
    assert len(d["spans"]) == 2 and d["summary"]["total_ms"] == 100.0


def test_trace_detail_legacy_pseudo_spans(fake_logs):
    with TestClient(app) as client:
        d = client.get("/v1/monitor/traces/2").json()
    assert d["legacy"] is True
    names = [s["name"] for s in d["spans"]]
    assert "recall" in names and "rerank" in names
    assert d["summary"] is None


def test_trace_detail_404(fake_logs):
    with TestClient(app) as client:
        assert client.get("/v1/monitor/traces/999").status_code == 404
