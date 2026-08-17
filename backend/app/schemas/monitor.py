"""系统监控（``/v1/monitor/*``）响应 schema（Phase 8.4）。

4 个只读端点，全部聚合自既有数据源（``retrieval_logs``/``chat_messages``/``*_chunks`` +
Milvus/ES/Redis 现有客户端），**无新表/迁移/依赖**。任一组件失败 → 该字段 ``None``，
端点仍返回（``resources`` 整体降级为 ``degraded``），**永不 500**。
"""
from __future__ import annotations

from pydantic import BaseModel

# ---- GET /monitor/retrieval-perf ----


class LatencyMs(BaseModel):
    """检索延迟分布（毫秒）。"""

    avg_total: float | None
    p50_total: float | None
    p95_total: float | None
    avg_recall: float | None  # Stage0 + 三路召回 + RRF
    avg_rerank: float | None  # 粗排+精排合计


class RetrievalFunnel(BaseModel):
    """召回漏斗均值（仅 legacy/retrieve 路径，agent 路径漏斗置 0 不计）。"""

    avg_pool: float | None  # RRF 融合池大小（recall_count 均值）
    avg_final: float | None  # 精排后最终候选数（fine_rank_count 均值）


class FeedbackCounts(BaseModel):
    helpful: int
    not_helpful: int


class RetrievalPerfResponse(BaseModel):
    """检索性能漏斗（窗口内）。"""

    window: str  # today / 7d / all
    queries: int  # 检索日志总数
    latency_ms: LatencyMs
    funnel: RetrievalFunnel
    rerank_rate: float | None  # 启用精排的检索占比
    feedback: FeedbackCounts


# ---- GET /monitor/resources ----


class ComponentInfo(BaseModel):
    """单组件连通 + 大小（按组件取相关字段，余为 None）。"""

    up: bool | None = None
    detail: str | None = None  # 失败原因
    db_size_bytes: int | None = None  # postgres
    used_memory_bytes: int | None = None  # redis
    keys: int | None = None  # redis
    collections: int | None = None  # milvus
    rows: int | None = None  # milvus
    doc_count: int | None = None  # elasticsearch
    size_bytes: int | None = None  # elasticsearch store
    asset_bytes: int | None = None  # minio（PG 派生：doc_resources + doc_files 字节和）


class ResourcesResponse(BaseModel):
    """基础设施资源连通与占用。"""

    status: str  # healthy / degraded
    components: dict[str, ComponentInfo]


# ---- GET /monitor/api-usage ----


class ApiUsageResponse(BaseModel):
    """外部 API 用量（查询侧 PG 派生代理，见 ``note``）。"""

    window: str
    llm_calls: int  # LLM 生成调用 ≈ assistant 消息数
    embedding_query_calls: int  # 查询嵌入调用 ≈ retrieval_logs 行数（dual 模式 ×2 近似）
    rerank_calls: int  # 精排调用 = rerank_on 的检索行数
    generated_tokens_est: int  # 生成 token 估算（assistant 内容字符数 / 4）
    indexed_tokens: int  # 已索引 token（code_chunks + doc_chunks token_count 和）
    note: str  # 口径与缺口说明


# ---- GET /monitor/index-stats ----


class MilvusCollectionStat(BaseModel):
    name: str
    dim: int | None
    rows: int | None  # best-effort（collection 未加载时为 None）


class PostgresIndexStats(BaseModel):
    code_chunks: int
    code_chunks_active: int
    code_chunks_synced_pct: float | None
    doc_chunks: int
    doc_chunks_active: int
    doc_chunks_synced_pct: float | None
    chunk_relations: int
    chunk_relations_stale: int
    call_graph: int
    call_graph_active: int
    code_files: int
    doc_files: int
    doc_resources: int
    retrieval_logs: int
    conversations: int
    chat_messages: int


class MilvusIndexStats(BaseModel):
    strategy: str
    collections: list[MilvusCollectionStat]


class EsIndexStats(BaseModel):
    index: str
    doc_count: int | None
    by_kind: dict[str, int | None]


class IndexStatsResponse(BaseModel):
    """各索引/表规模（PG 行数 + Milvus 向量数 + ES 文档数）。"""

    postgres: PostgresIndexStats
    milvus: MilvusIndexStats
    elasticsearch: EsIndexStats


# ---- GET /monitor/traces（M41 全链路追溯）----


class TraceTokens(BaseModel):
    prompt: int
    completion: int
    n_llm_calls: int
    estimated: bool


class TraceListItem(BaseModel):
    log_id: int
    query: str  # 截断摘要
    mode: str | None = None
    agent: str | None = None  # agent span name（dict 行）/ None
    total_ms: float | None = None
    tokens: TraceTokens | None = None
    has_trace: bool
    created_at: str | None = None


class TraceListResponse(BaseModel):
    window: str
    total: int
    items: list[TraceListItem]


class TraceDetail(BaseModel):
    log_id: int
    query: str
    mode: str | None = None
    legacy: bool  # True = 旧格式（伪 span，部分链路）
    spans: list[dict]
    summary: dict | None = None
    created_at: str | None = None
