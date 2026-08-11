"""系统监控聚合服务（Phase 8.4，api接口清单 §八 监控）。

**只读，无新表/迁移/依赖**——4 个端点全部聚合自既有数据源：

- ``retrieval-perf``：raw ``text()`` + ``percentile_cont`` 聚合 ``retrieval_logs``（延迟分布 +
  漏斗均值 + 精排/反馈计数）。漏斗均值用 ``CASE WHEN mode IS DISTINCT FROM 'agent'`` 排除
  agent 路径零漏斗行（真漏斗在工具内，见 ``_base._emit_retrieval_meta``）。
- ``api-usage``：**PG 派生查询侧代理**——外部 API 用量当前无埋点（3 客户端均丢弃 usage），
  故从 ``chat_messages``（assistant 消息=LLM 生成调用）+ ``retrieval_logs``（查询嵌入/精排调用）
  + ``*_chunks.token_count``（已索引 token）派生。token 为估算；入库侧嵌入/重排未记录（见
  ``_USAGE_NOTE``）。精确实时计量/客户端埋点延后。
- ``index-stats``：PG 逐表计数（单查询子查询）+ Milvus ``num_entities`` + ES ``count``。
- ``resources``：真实客户端连通 + 各存储大小（PG ``pg_database_size`` / Redis ``INFO memory`` /
  Milvus 行数 / ES ``store.size`` / MinIO=PG 字节和）。每组件独立 try/except → 失败 ``up:false``，
  整体降级 ``degraded``，**永不 500**。

镜像 ``agent_stats_service``（``_since`` 窗口）+ ``staleness_sweep_service``（raw ``text()``）+
``sync.py``（``func.count``）。外部同步客户端（Milvus/ES/MinIO）一律 ``asyncio.to_thread``；
Redis 用 ``redis.asyncio``。

窗口 ``today|7d|all``（``all``→``since=None``，SQL 用 ``cast(:since AS timestamptz) IS NULL OR …``
使 None 透传为「无窗口」）。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import es_client, milvus_client, minio_client
from app.core.config import settings
from app.schemas.monitor import (
    ApiUsageResponse,
    ComponentInfo,
    EsIndexStats,
    FeedbackCounts,
    IndexStatsResponse,
    LatencyMs,
    MilvusCollectionStat,
    MilvusIndexStats,
    PostgresIndexStats,
    ResourcesResponse,
    RetrievalFunnel,
    RetrievalPerfResponse,
)

_USAGE_NOTE = (
    "查询侧 PG 派生代理：LLM 调用≈assistant 消息数，查询嵌入≈retrieval_logs 行数（dual 模式 ×2 近似），"
    "精排=rerank_on 行数。生成 token 为估算（字符数/4）。入库侧嵌入/重排调用未记录。"
)


def _since(window: str) -> datetime | None:
    """窗口起点（UTC）：today=今日 0 点；7d=7 天前；all=None（无窗口）。镜像 agent_stats_service。"""
    now = datetime.now(UTC)
    if window == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "7d":
        return now - timedelta(days=7)
    return None


def _err(e: Exception) -> str:
    """失败原因摘要（用 ``type(e).__name__``，避免 sync_service 曾踩的 ``type`` 形参遮蔽坑）。"""
    msg = f"{type(e).__name__}: {e}"
    return msg[:200]


# ============================================================================
# GET /monitor/retrieval-perf
# ============================================================================

# 漏斗均值（avg_pool/avg_final）仅对 legacy/retrieve 路径（mode IS DISTINCT FROM 'agent'）取值，
# agent 路径 recall/fine 全置 0 会污染均值（NULL 被 avg 忽略 → 自动排除）。
# percentile_cont 自动忽略 NULL 延迟；空窗口 → 各聚合 None、count=0。
_PERF_SQL = text(
    """
    SELECT
      count(*)                                                            AS queries,
      avg(total_latency_ms)                                               AS avg_total,
      percentile_cont(0.5)  WITHIN GROUP (ORDER BY total_latency_ms)      AS p50_total,
      percentile_cont(0.95) WITHIN GROUP (ORDER BY total_latency_ms)      AS p95_total,
      avg(recall_latency_ms)                                              AS avg_recall,
      avg(fine_rank_ms)                                                   AS avg_rerank,
      avg(CASE WHEN recall_results->>'mode' IS DISTINCT FROM 'agent'
               THEN recall_count END)                                     AS avg_pool,
      avg(CASE WHEN recall_results->>'mode' IS DISTINCT FROM 'agent'
               THEN fine_rank_count END)                                  AS avg_final,
      sum(CASE WHEN recall_results->>'rerank_on' = 'true' THEN 1 ELSE 0 END)    AS rerank_on,
      sum(CASE WHEN user_feedback = 'HELPFUL' THEN 1 ELSE 0 END)                AS helpful,
      sum(CASE WHEN user_feedback = 'NOT_HELPFUL' THEN 1 ELSE 0 END)            AS not_helpful
      FROM retrieval_logs
     WHERE (cast(:since AS timestamptz) IS NULL OR created_at >= cast(:since AS timestamptz))
    """
)


async def get_retrieval_perf(session: AsyncSession, window: str = "today") -> RetrievalPerfResponse:
    """检索性能漏斗（窗口内）：延迟分布 p50/p95 + 漏斗均值 + 精排/反馈计数。"""
    since = _since(window)
    r = (await session.execute(_PERF_SQL, {"since": since})).mappings().first() or {}

    def _f(v) -> float | None:
        return round(float(v), 2) if v is not None else None

    queries = int(r.get("queries") or 0)
    rerank_on = int(r.get("rerank_on") or 0)
    return RetrievalPerfResponse(
        window=window,
        queries=queries,
        latency_ms=LatencyMs(
            avg_total=_f(r.get("avg_total")),
            p50_total=_f(r.get("p50_total")),
            p95_total=_f(r.get("p95_total")),
            avg_recall=_f(r.get("avg_recall")),
            avg_rerank=_f(r.get("avg_rerank")),
        ),
        funnel=RetrievalFunnel(
            avg_pool=_f(r.get("avg_pool")),
            avg_final=_f(r.get("avg_final")),
        ),
        rerank_rate=round(rerank_on / queries, 4) if queries else None,
        feedback=FeedbackCounts(
            helpful=int(r.get("helpful") or 0),
            not_helpful=int(r.get("not_helpful") or 0),
        ),
    )


# ============================================================================
# GET /monitor/api-usage（PG 派生查询侧代理）
# ============================================================================

_LLM_SQL = text(
    """
    SELECT count(*) AS calls, coalesce(sum(char_length(content)), 0) AS chars
      FROM chat_messages
     WHERE role = 'assistant'
       AND (cast(:since AS timestamptz) IS NULL OR created_at >= cast(:since AS timestamptz))
    """
)
_RETR_SQL = text(
    """
    SELECT count(*) AS calls,
           sum(CASE WHEN recall_results->>'rerank_on' = 'true' THEN 1 ELSE 0 END) AS rerank
      FROM retrieval_logs
     WHERE (cast(:since AS timestamptz) IS NULL OR created_at >= cast(:since AS timestamptz))
    """
)
# 已索引 token 快照（累计，无窗口）：code + doc chunks 的 token_count 和。
_INDEXED_TOKENS_SQL = text(
    """
    SELECT coalesce((SELECT sum(token_count) FROM code_chunks), 0)
         + coalesce((SELECT sum(token_count) FROM doc_chunks), 0)
    """
)


async def get_api_usage(session: AsyncSession, window: str = "today") -> ApiUsageResponse:
    """外部 API 用量（查询侧 PG 派生代理，见 ``_USAGE_NOTE``）。"""
    since = _since(window)
    llm = (await session.execute(_LLM_SQL, {"since": since})).mappings().first() or {}
    retr = (await session.execute(_RETR_SQL, {"since": since})).mappings().first() or {}
    indexed = (await session.execute(_INDEXED_TOKENS_SQL)).scalar_one()
    llm_calls = int(llm.get("calls") or 0)
    chars = int(llm.get("chars") or 0)
    return ApiUsageResponse(
        window=window,
        llm_calls=llm_calls,
        embedding_query_calls=int(retr.get("calls") or 0),
        rerank_calls=int(retr.get("rerank") or 0),
        generated_tokens_est=chars // 4,  # ~4 字符/token 估算
        indexed_tokens=int(indexed or 0),
        note=_USAGE_NOTE,
    )


# ============================================================================
# GET /monitor/index-stats
# ============================================================================

# 单查询、多子查询：一次往返取全表计数 + 布尔条件计数（活跃/已同步/过时）。
_PG_COUNTS_SQL = text(
    """
    SELECT
      (SELECT count(*) FROM code_chunks)                                       AS code_chunks,
      (SELECT count(*) FROM code_chunks WHERE is_deleted = false)              AS code_chunks_active,
      (SELECT count(*) FROM code_chunks WHERE embedding_synced AND is_deleted = false) AS code_chunks_synced,
      (SELECT count(*) FROM doc_chunks)                                        AS doc_chunks,
      (SELECT count(*) FROM doc_chunks WHERE is_deleted = false)               AS doc_chunks_active,
      (SELECT count(*) FROM doc_chunks WHERE embedding_synced AND is_deleted = false)  AS doc_chunks_synced,
      (SELECT count(*) FROM chunk_relations)                                   AS chunk_relations,
      (SELECT count(*) FROM chunk_relations WHERE is_stale)                    AS chunk_relations_stale,
      (SELECT count(*) FROM call_graph)                                        AS call_graph,
      (SELECT count(*) FROM call_graph WHERE is_deleted = false)               AS call_graph_active,
      (SELECT count(*) FROM code_files)                                        AS code_files,
      (SELECT count(*) FROM doc_files)                                         AS doc_files,
      (SELECT count(*) FROM doc_resources)                                     AS doc_resources,
      (SELECT count(*) FROM retrieval_logs)                                    AS retrieval_logs,
      (SELECT count(*) FROM conversations)                                     AS conversations,
      (SELECT count(*) FROM chat_messages)                                     AS chat_messages
    """
)


def _milvus_index() -> list[dict]:
    """各 collection 行数（best-effort：未加载/不存在 → rows=None）。失败 → []。"""
    try:
        c = milvus_client.get_client()
        if settings.embedding_strategy == "dual":
            targets = [(milvus_client.CODE_COLLECTION, milvus_client.CODE_DIM),
                       (milvus_client.DOC_COLLECTION, milvus_client.DOC_DIM)]
        else:
            targets = [(milvus_client.UNIFIED_COLLECTION, milvus_client.UNIFIED_DIM)]
        out: list[dict] = []
        for name, dim in targets:
            rows: int | None = None
            try:
                rows = int(c.num_entities(name) or 0)
            except Exception:
                rows = None
            out.append({"name": name, "dim": dim, "rows": rows})
        return out
    except Exception:
        return []


def _es_index() -> dict:
    """ES 索引文档数（总数 + 按 kind）。失败 → None 占位。"""
    try:
        es = es_client.get_es()
        total = int(es.count(index=es_client.INDEX)["count"])
        code = int(es.count(index=es_client.INDEX, query={"term": {"kind": "code"}})["count"])
        doc = int(es.count(index=es_client.INDEX, query={"term": {"kind": "doc"}})["count"])
        return {"index": es_client.INDEX, "doc_count": total, "by_kind": {"code": code, "doc": doc}}
    except Exception:
        return {"index": es_client.INDEX, "doc_count": None, "by_kind": {"code": None, "doc": None}}


async def get_index_stats(session: AsyncSession) -> IndexStatsResponse:
    """各索引/表规模：PG 行数 + Milvus 向量数 + ES 文档数。"""
    row = (await session.execute(_PG_COUNTS_SQL)).mappings().first() or {}
    milvus = await asyncio.to_thread(_milvus_index)
    es = await asyncio.to_thread(_es_index)

    def _pct(synced: int, active: int) -> float | None:
        return round(synced / active * 100, 1) if active else None

    pg = PostgresIndexStats(
        code_chunks=int(row.get("code_chunks") or 0),
        code_chunks_active=int(row.get("code_chunks_active") or 0),
        code_chunks_synced_pct=_pct(int(row.get("code_chunks_synced") or 0),
                                    int(row.get("code_chunks_active") or 0)),
        doc_chunks=int(row.get("doc_chunks") or 0),
        doc_chunks_active=int(row.get("doc_chunks_active") or 0),
        doc_chunks_synced_pct=_pct(int(row.get("doc_chunks_synced") or 0),
                                   int(row.get("doc_chunks_active") or 0)),
        chunk_relations=int(row.get("chunk_relations") or 0),
        chunk_relations_stale=int(row.get("chunk_relations_stale") or 0),
        call_graph=int(row.get("call_graph") or 0),
        call_graph_active=int(row.get("call_graph_active") or 0),
        code_files=int(row.get("code_files") or 0),
        doc_files=int(row.get("doc_files") or 0),
        doc_resources=int(row.get("doc_resources") or 0),
        retrieval_logs=int(row.get("retrieval_logs") or 0),
        conversations=int(row.get("conversations") or 0),
        chat_messages=int(row.get("chat_messages") or 0),
    )
    return IndexStatsResponse(
        postgres=pg,
        milvus=MilvusIndexStats(
            strategy=settings.embedding_strategy,
            collections=[MilvusCollectionStat(**c) for c in milvus],
        ),
        elasticsearch=EsIndexStats(**es),
    )


# ============================================================================
# GET /monitor/resources（真实客户端连通 + 各存储大小）
# ============================================================================


async def _pg_resources(session: AsyncSession) -> dict:
    """PG：DB 大小 + MinIO 索引资产字节和（doc_resources + doc_files）。"""
    try:
        db_size = (await session.execute(
            text("SELECT pg_database_size(current_database())")
        )).scalar_one()
        asset = (await session.execute(text(
            "SELECT coalesce((SELECT sum(file_size_bytes) FROM doc_resources), 0) "
            "+ coalesce((SELECT sum(file_size_bytes) FROM doc_files), 0)"
        ))).scalar_one()
        return {"up": True, "db_size_bytes": int(db_size or 0), "asset_bytes": int(asset or 0)}
    except Exception as e:
        return {"up": False, "detail": _err(e)}


async def _redis_resources() -> dict:
    """Redis：连通 + ``used_memory`` + ``dbsize``。自建抛弃式 async 客户端（无全局客户端）。"""
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=1.5)
        try:
            await r.ping()
            info = await r.info(section="memory")
            keys = await r.dbsize()
            return {"up": True, "used_memory_bytes": int(info.get("used_memory", 0) or 0), "keys": int(keys)}
        finally:
            await r.aclose()
    except Exception as e:
        return {"up": False, "detail": _err(e)}


def _milvus_resources() -> dict:
    """Milvus：连通（list_collections）+ collection 数 + 总行数（best-effort）。"""
    try:
        c = milvus_client.get_client()
        names = c.list_collections()
        rows = 0
        for n in names:
            try:
                rows += int(c.num_entities(n) or 0)
            except Exception:
                pass
        return {"up": True, "collections": len(names), "rows": rows}
    except Exception as e:
        return {"up": False, "detail": _err(e)}


def _es_resources() -> dict:
    """ES：连通 + 文档数 + store 大小（bytes=b 原始字节）。"""
    try:
        es = es_client.get_es()
        doc_count = int(es.count(index=es_client.INDEX)["count"])
        cat = es.cat.indices(index=es_client.INDEX, format="json", bytes="b", h=["store.size"])
        size = 0
        if cat:
            try:
                size = int(cat[0].get("store.size", 0) or 0)
            except (ValueError, TypeError):
                size = 0
        return {"up": True, "doc_count": doc_count, "size_bytes": size}
    except Exception as e:
        return {"up": False, "detail": _err(e)}


def _minio_resources() -> dict:
    """MinIO：连通（bucket_exists）。资产字节数取自 PG（见 _pg_resources）。"""
    try:
        ok = bool(minio_client.get_client().bucket_exists(settings.minio_bucket))
        return {"up": ok}
    except Exception as e:
        return {"up": False, "detail": _err(e)}


async def get_resources(session: AsyncSession) -> ResourcesResponse:
    """基础设施连通与占用（每组件独立 try/except，失败不阻断，整体降级 degraded）。"""
    pg = await _pg_resources(session)
    redis = await _redis_resources()
    milvus = await asyncio.to_thread(_milvus_resources)
    es = await asyncio.to_thread(_es_resources)
    minio = await asyncio.to_thread(_minio_resources)
    components = {
        "postgres": ComponentInfo(up=pg.get("up"), db_size_bytes=pg.get("db_size_bytes"), detail=pg.get("detail")),
        "redis": ComponentInfo(**redis),
        "milvus": ComponentInfo(**milvus),
        "elasticsearch": ComponentInfo(**es),
        "minio": ComponentInfo(up=minio.get("up"), asset_bytes=pg.get("asset_bytes"), detail=minio.get("detail")),
    }
    status = "healthy" if all(c.up for c in components.values()) else "degraded"
    return ResourcesResponse(status=status, components=components)
