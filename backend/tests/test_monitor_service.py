"""系统监控聚合服务单测（Phase 8.4，无基础设施）。

镜像 ``test_agent_stats``（假 session 结果队列）+ ``test_staleness_sweep``：服务内多次
``session.execute`` 顺序固定，假 session 按序弹出；外部客户端（Milvus/ES/Redis/MinIO）
经 monkeypatch 隔离真 IO，覆盖正常 + 降级路径。
"""
from __future__ import annotations

from app.api.v1.monitor import router as monitor_router
from app.services import monitor_service as ms

# ---- _since helper ----


def test_since_all_returns_none():
    assert ms._since("all") is None


def test_since_today_is_utc_midnight():
    s = ms._since("today")
    assert s is not None and s.tzinfo is not None
    assert (s.hour, s.minute, s.second, s.microsecond) == (0, 0, 0, 0)


def test_since_7d_returns_past():
    s = ms._since("7d")
    assert s is not None and s.tzinfo is not None


# ---- 假 session（结果队列；_Rows 兼容 mappings().first()/scalar_one()/one()） ----


class _Rows:
    def __init__(self, *, mapping=None, scalar=None, all_rows=None):
        self._m = mapping or {}
        self._s = scalar
        self._a = all_rows or []

    def mappings(self):
        return self

    def first(self):
        return self._m

    def one(self):
        return self._m

    def scalar_one(self):
        return self._s

    def scalar(self):
        return self._s

    def all(self):
        return self._a


class _FakeSession:
    def __init__(self, results):
        self._r = list(results)

    async def execute(self, *a, **k):
        return self._r.pop(0)


# ---- get_retrieval_perf ----


async def test_get_retrieval_perf_composes_latency_and_funnel():
    session = _FakeSession([_Rows(mapping={
        "queries": 10, "avg_total": 250.0, "p50_total": 200.0, "p95_total": 400.0,
        "avg_recall": 150.0, "avg_rerank": 90.0, "avg_pool": 12.5, "avg_final": 7.0,
        "rerank_on": 8, "helpful": 5, "not_helpful": 1,
    })])
    resp = await ms.get_retrieval_perf(session, "all")
    assert resp.window == "all" and resp.queries == 10
    assert resp.latency_ms.avg_total == 250.0 and resp.latency_ms.p95_total == 400.0
    assert resp.funnel.avg_pool == 12.5 and resp.funnel.avg_final == 7.0
    assert resp.rerank_rate == round(8 / 10, 4)  # 0.8
    assert resp.feedback.helpful == 5 and resp.feedback.not_helpful == 1


async def test_get_retrieval_perf_empty_window_is_graceful():
    session = _FakeSession([_Rows(mapping={
        "queries": 0, "avg_total": None, "p50_total": None, "p95_total": None,
        "avg_recall": None, "avg_rerank": None, "avg_pool": None, "avg_final": None,
        "rerank_on": 0, "helpful": 0, "not_helpful": 0,
    })])
    resp = await ms.get_retrieval_perf(session, "today")
    assert resp.queries == 0
    assert resp.rerank_rate is None  # 0/0 → None
    assert resp.latency_ms.p50_total is None and resp.funnel.avg_pool is None


# ---- get_api_usage ----


async def test_get_api_usage_derives_counts_and_token_estimate():
    session = _FakeSession([
        _Rows(mapping={"calls": 12, "chars": 4800}),   # assistant 消息：12 条、4800 字符
        _Rows(mapping={"calls": 12, "rerank": 9}),      # retrieval：12 行、9 行 rerank_on
        _Rows(scalar=123456),                           # 已索引 token
    ])
    resp = await ms.get_api_usage(session, "7d")
    assert resp.llm_calls == 12
    assert resp.embedding_query_calls == 12
    assert resp.rerank_calls == 9
    assert resp.generated_tokens_est == 1200  # 4800 // 4
    assert resp.indexed_tokens == 123456
    assert "代理" in resp.note


# ---- get_index_stats ----


def _pg_counts(**over):
    base = {
        "code_chunks": 100, "code_chunks_active": 90, "code_chunks_synced": 80,
        "doc_chunks": 50, "doc_chunks_active": 45, "doc_chunks_synced": 40,
        "chunk_relations": 30, "chunk_relations_stale": 3,
        "call_graph": 20, "call_graph_active": 18,
        "code_files": 10, "doc_files": 8, "doc_resources": 15,
        "retrieval_logs": 200, "conversations": 50, "chat_messages": 120,
    }
    base.update(over)
    return base


async def test_get_index_stats_composes_pg_milvus_es(monkeypatch):
    monkeypatch.setattr(ms, "_milvus_index", lambda: [{"name": "coderag_vectors", "dim": 1024, "rows": 140}])
    monkeypatch.setattr(ms, "_es_index", lambda: {"index": "coderag_chunks", "doc_count": 150,
                                                  "by_kind": {"code": 100, "doc": 50}})
    session = _FakeSession([_Rows(mapping=_pg_counts())])
    resp = await ms.get_index_stats(session)
    assert resp.postgres.code_chunks == 100 and resp.postgres.code_chunks_active == 90
    assert resp.postgres.code_chunks_synced_pct == round(80 / 90 * 100, 1)  # 88.9
    assert resp.postgres.doc_chunks_synced_pct == round(40 / 45 * 100, 1)
    assert resp.postgres.chunk_relations_stale == 3
    assert resp.milvus.strategy == ms.settings.embedding_strategy  # 随配置，不硬编码
    assert resp.milvus.collections[0].name == "coderag_vectors" and resp.milvus.collections[0].rows == 140
    assert resp.elasticsearch.doc_count == 150 and resp.elasticsearch.by_kind["code"] == 100


async def test_get_index_stats_synced_pct_none_when_no_active(monkeypatch):
    monkeypatch.setattr(ms, "_milvus_index", lambda: [])
    monkeypatch.setattr(ms, "_es_index", lambda: {"index": "coderag_chunks", "doc_count": None,
                                                  "by_kind": {"code": None, "doc": None}})
    session = _FakeSession([_Rows(mapping=_pg_counts(code_chunks_active=0, code_chunks_synced=0))])
    resp = await ms.get_index_stats(session)
    assert resp.postgres.code_chunks_synced_pct is None  # 0/0


# ---- get_resources ----


async def _ok_redis():
    return {"up": True, "used_memory_bytes": 1000, "keys": 5}


async def test_get_resources_healthy(monkeypatch):
    monkeypatch.setattr(ms, "_redis_resources", _ok_redis)
    monkeypatch.setattr(ms, "_milvus_resources", lambda: {"up": True, "collections": 1, "rows": 140})
    monkeypatch.setattr(ms, "_es_resources", lambda: {"up": True, "doc_count": 150, "size_bytes": 9999})
    monkeypatch.setattr(ms, "_minio_resources", lambda: {"up": True})
    session = _FakeSession([_Rows(scalar=5000000), _Rows(scalar=300000)])  # db_size, asset
    resp = await ms.get_resources(session)
    assert resp.status == "healthy"
    assert resp.components["postgres"].up and resp.components["postgres"].db_size_bytes == 5000000
    assert resp.components["redis"].used_memory_bytes == 1000
    assert resp.components["milvus"].rows == 140
    assert resp.components["elasticsearch"].size_bytes == 9999
    assert resp.components["minio"].asset_bytes == 300000


async def test_get_resources_degraded_when_component_down(monkeypatch):
    monkeypatch.setattr(ms, "_redis_resources", _ok_redis)
    monkeypatch.setattr(ms, "_milvus_resources", lambda: {"up": False, "detail": "ConnectionError: x"})
    monkeypatch.setattr(ms, "_es_resources", lambda: {"up": True, "doc_count": 1, "size_bytes": 1})
    monkeypatch.setattr(ms, "_minio_resources", lambda: {"up": True})
    session = _FakeSession([_Rows(scalar=10), _Rows(scalar=20)])
    resp = await ms.get_resources(session)
    assert resp.status == "degraded"  # milvus down
    assert resp.components["milvus"].up is False and resp.components["milvus"].detail


# ---- 外部客户端 helper 降级（无真 IO） ----


def test_milvus_index_returns_empty_when_client_down(monkeypatch):
    monkeypatch.setattr(ms.milvus_client, "get_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert ms._milvus_index() == []


def test_es_index_returns_none_placeholders_when_down(monkeypatch):
    monkeypatch.setattr(ms.es_client, "get_es", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    out = ms._es_index()
    assert out["doc_count"] is None and out["by_kind"]["code"] is None


def test_minio_resources_down_yields_up_false(monkeypatch):
    monkeypatch.setattr(ms.minio_client, "get_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    out = ms._minio_resources()
    assert out["up"] is False and out["detail"]


class _BadRedis:
    async def ping(self):
        raise RuntimeError("down")

    async def info(self, section=None):
        raise RuntimeError("down")

    async def dbsize(self):
        raise RuntimeError("down")

    async def aclose(self):
        pass


async def test_redis_resources_down_yields_up_false(monkeypatch):
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: _BadRedis())
    out = await ms._redis_resources()
    assert out["up"] is False and out["detail"]


# ---- 路由注册 ----


def test_monitor_router_endpoints_registered():
    paths = {r.path for r in monitor_router.routes}
    assert "/monitor/retrieval-perf" in paths
    assert "/monitor/api-usage" in paths
    assert "/monitor/index-stats" in paths
    assert "/monitor/resources" in paths
