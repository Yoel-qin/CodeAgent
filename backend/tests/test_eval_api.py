"""评测端点单测（api/v1/eval.py）：POST /run + GET /runs + GET /runs/{id}。

TestClient（dependency_overrides[get_db]）+ monkeypatch eval_run_service 三个函数返 canned
``EvalRun``（仿 test_search_api.py）。验证 200/shape、列表无 per_query、详情有 per_query、404。
"""
from __future__ import annotations

from datetime import UTC, datetime

import app.services.eval_run_service as svc
from app.db.models.eval import EvalRun


def _run(rid: int) -> EvalRun:
    return EvalRun(
        run_id=rid, status="COMPLETED", trigger="api", top_k=10, rewrite="off",
        embedding_strategy="unified", n_queries=82, n_evaluable=80, rerank_on_count=80,
        duration_ms=1234,
        aggregate={"n": 80, "recall": {"1": 0.8, "10": 0.9}, "precision": {"10": 0.5},
                   "mrr": 0.85, "ndcg": {"10": 0.88}},
        config={"top_k": 10, "rewrite": "off", "embedding_strategy": "unified"},
        per_query=[{"id": "a01", "text": "x", "recall": {"10": 1.0}, "mrr": 1.0,
                    "first_hit_rank": 1, "rerank_on": True, "error": None}],
        unresolved=[], error_message=None,
        started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


async def test_eval_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.deps import get_db
    from app.main import app

    async def fake_run_and_persist(session, *, top_k=10, rewrite="off", eval_set=None, persist=True, **_):
        return _run(1)

    async def fake_list_runs(session, *, limit=50, kind=None):
        return [_run(2), _run(1)]

    async def fake_get_run(session, rid):
        return _run(rid) if rid == 1 else None

    monkeypatch.setattr(svc, "run_and_persist", fake_run_and_persist)
    monkeypatch.setattr(svc, "list_runs", fake_list_runs)
    monkeypatch.setattr(svc, "get_run", fake_get_run)

    async def _override():
        return None  # session 被 mocked 的 service 忽略

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)

        # POST /v1/eval/run → 200 + 完整 detail（含 per_query）
        r = client.post("/v1/eval/run", json={"top_k": 10, "rewrite": "off"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "COMPLETED" and body["run_id"] == 1
        assert body["aggregate"]["recall"]["10"] == 0.9          # 字符串 key
        assert body["per_query"] and body["per_query"][0]["id"] == "a01"
        assert body["embedding_strategy"] == "unified"

        # GET /v1/eval/runs → 列表，items 无 per_query、有 aggregate（轻量 + 趋势可用）
        r2 = client.get("/v1/eval/runs", params={"limit": 50})
        assert r2.status_code == 200
        lst = r2.json()
        assert lst["total"] == 2 and len(lst["items"]) == 2
        item = lst["items"][0]
        assert "per_query" not in item
        assert "aggregate" in item and item["aggregate"]["recall"]["10"] == 0.9
        assert item["unresolved_count"] == 0
        assert item["kind"] == "single"                        # M28：单次评测 kind 字段

        # GET /v1/eval/runs/{id} → 详情含 per_query
        r3 = client.get("/v1/eval/runs/1")
        assert r3.status_code == 200 and "per_query" in r3.json()

        # 未知 id → 404
        assert client.get("/v1/eval/runs/999").status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


# ===== A/B 消融端点（M28）=====


def _ab_run(rid: int) -> EvalRun:
    """canned A/B EvalRun（config.kind="ab"，含 report.variants/pairs）。"""
    return EvalRun(
        run_id=rid, status="COMPLETED", trigger="api", top_k=10, rewrite="off",
        embedding_strategy="dual", n_queries=82, n_evaluable=80, rerank_on_count=80,
        duration_ms=20000,
        aggregate={"n": 80, "recall": {"10": 1.0}, "precision": {"10": 0.2},
                   "mrr": 0.9, "ndcg": {"10": 0.93}},
        config={
            "kind": "ab", "top_k": 10, "rewrite": "off", "embedding_strategy": "dual",
            "pairs": ["rerank"],
            "report": {
                "variants": {
                    "full": {"ablation": {"vector": True, "lexical": True, "graph": True, "rerank": True},
                             "desc": "全开", "aggregate": {"n": 80, "recall": {"10": 1.0},
                             "precision": {"10": 0.2}, "mrr": 0.9, "ndcg": {"10": 0.93}},
                             "n_evaluable": 80, "n_queries": 82, "rerank_on_count": 80,
                             "unresolved": 0, "per_query": [{"id": "a01", "rerank_on": True}]},
                    "no_rerank": {"ablation": {"vector": True, "lexical": True, "graph": True, "rerank": False},
                                  "desc": "关精排", "aggregate": {"n": 80, "recall": {"10": 1.0},
                                  "precision": {"10": 0.08}, "mrr": 0.15, "ndcg": {"10": 0.22}},
                                  "n_evaluable": 80, "n_queries": 82, "rerank_on_count": 0,
                                  "unresolved": 0, "per_query": []},
                },
                "pairs": [{"name": "rerank", "claim": "精排 +15~25%", "baseline": "no_rerank",
                           "treatment": "full", "metric_focus": ["precision", "ndcg", "mrr"],
                           "delta": {"precision": {"10": {"abs": 0.12, "pct": 150.0}}}}],
            },
        },
        per_query=None, unresolved=[], error_message=None,
        started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


async def test_eval_ab_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.deps import get_db
    from app.main import app

    async def fake_run_ab_and_persist(session, *, top_k=10, rewrite="off", eval_set=None,
                                      pairs=None, graph_subset=False, diagnose=False, persist=True, **_):
        return _ab_run(1)

    async def fake_list_runs(session, *, limit=50, kind=None):
        return [_ab_run(2), _ab_run(1)] if kind == "ab" else []

    async def fake_get_run(session, rid):
        return _ab_run(rid) if rid == 1 else None

    monkeypatch.setattr(svc, "run_ab_and_persist", fake_run_ab_and_persist)
    monkeypatch.setattr(svc, "list_runs", fake_list_runs)
    monkeypatch.setattr(svc, "get_run", fake_get_run)

    async def _override():
        return None

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)

        # POST /v1/eval/ab → 200 + ABRunDetail（含 variants + pairs）
        r = client.post("/v1/eval/ab", json={"pairs": ["rerank"], "top_k": 10})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "ab" and body["status"] == "COMPLETED"
        assert "full" in body["variants"] and "no_rerank" in body["variants"]
        assert body["pairs"][0]["name"] == "rerank"
        assert body["pairs"][0]["delta"]["precision"]["10"]["pct"] == 150.0
        assert body["aggregate"]["recall"]["10"] == 1.0       # full 变体锚点

        # GET /v1/eval/ab-runs → 列表，items 无 variants per_query、有 pairs
        r2 = client.get("/v1/eval/ab-runs", params={"limit": 50})
        assert r2.status_code == 200
        lst = r2.json()
        assert lst["total"] == 2
        assert lst["items"][0]["kind"] == "ab"
        assert "variants" not in lst["items"][0]              # 列表不含变体明细
        assert lst["items"][0]["pairs"][0]["name"] == "rerank"

        # GET /v1/eval/ab-runs/{id} → 详情含 variants
        r3 = client.get("/v1/eval/ab-runs/1")
        assert r3.status_code == 200 and "variants" in r3.json()

        # 未知 id → 404
        assert client.get("/v1/eval/ab-runs/999").status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


# ===== 单次评测 ablation 参数（M29）=====


async def test_eval_run_ablation(monkeypatch):
    """POST /run 带 ablation 透传给 service；summary 列表暴露 ablation 字段。"""
    from fastapi.testclient import TestClient

    from app.api.deps import get_db
    from app.main import app

    captured = {}

    async def fake_run_and_persist(session, *, top_k=10, rewrite="off", eval_set=None,
                                   ablation=None, persist=True, **_):
        captured["ablation"] = ablation
        # 返带 config.ablation 的 canned run
        r = _run(1)
        r.config = {**(r.config or {}), "ablation": ablation}
        return r

    async def fake_list_runs(session, *, limit=50, kind=None):
        r = _run(2)
        r.config = {**(r.config or {}), "ablation": {"rerank": False}}
        return [r]

    monkeypatch.setattr(svc, "run_and_persist", fake_run_and_persist)
    monkeypatch.setattr(svc, "list_runs", fake_list_runs)

    async def _override():
        return None

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)

        # POST /run 带 ablation → 透传给 service
        r = client.post("/v1/eval/run", json={"top_k": 10, "ablation": {"rerank": False}})
        assert r.status_code == 200, r.text
        assert captured["ablation"] == {"rerank": False}
        assert r.json()["ablation"] == {"rerank": False}

        # POST /run 未知 ablation 字段 → service 抛 ValueError → 422
        async def boom(session, **kw):
            raise ValueError("未知 ablation 字段")

        monkeypatch.setattr(svc, "run_and_persist", boom)
        r2 = client.post("/v1/eval/run", json={"ablation": {"bogus": True}})
        assert r2.status_code == 422

        # GET /runs 列表项含 ablation 字段
        r3 = client.get("/v1/eval/runs", params={"limit": 50})
        assert r3.status_code == 200
        assert r3.json()["items"][0]["ablation"] == {"rerank": False}
    finally:
        app.dependency_overrides.pop(get_db, None)
