"""检索评测响应 schema（Phase 9 评测产品化 M27 / api接口清单 §eval）。

aggregate 的 recall/precision/ndcg 是按 K 的 map；K 经 JSONB 序列化为字符串 key
（``{"1":..,"10":..}``），故用 ``dict[str, float | None]``，前端按 ``["10"]`` 索引。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class EvalRunRequest(BaseModel):
    """``POST /v1/eval/run`` 请求。"""

    top_k: int = 10
    rewrite: Literal["off", "auto"] = "off"
    eval_set: str | None = None  # 缺省用 backend/eval/eval_set.yaml
    persist: bool = True  # False → 预览，不写库但仍返完整 detail
    ablation: dict[str, bool] | None = None  # M29: {"rerank": False, ...} 跑单变体；None=全开=生产；未知键→422


class EvalAggregate(BaseModel):
    """宏平均指标（``metrics.aggregate`` 形状）。"""

    n: int
    recall: dict[str, float | None]
    precision: dict[str, float | None]
    mrr: float | None
    ndcg: dict[str, float | None]


class EvalRunSummary(BaseModel):
    """历史列表项（不含 per_query，保持列表轻量）。"""

    run_id: int
    status: str
    trigger: str
    top_k: int
    rewrite: str
    embedding_strategy: str
    n_queries: int
    n_evaluable: int
    rerank_on_count: int
    duration_ms: int | None
    unresolved_count: int  # = len(unresolved)；>0 标红幽灵查询运行
    aggregate: EvalAggregate | None = None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    error_message: str | None = None
    kind: str = "single"  # "single"(单次评测) | "ab"(A/B 消融，M28)；前端统一历史表的「类型」列
    ablation: dict[str, bool] | None = None  # M29: 单次评测跑变体时记录的消融配置（如 {"rerank": False}）；None=全开


class EvalRunDetail(EvalRunSummary):
    """单条详情（POST 运行结果 + GET /runs/{id}）：附 per_query / config / unresolved。"""

    config: dict[str, Any] | None = None
    per_query: list[dict[str, Any]] | None = None
    unresolved: list[dict[str, Any]] | None = None


class EvalRunListResponse(BaseModel):
    """``GET /v1/eval/runs`` 响应。"""

    total: int
    items: list[EvalRunSummary]


# ===== A/B 消融（M28；api接口清单 §eval-ab）=====
# ABReport（app/eval/ab_service.py）经 EvalRun.config["report"] 持久化（config.kind="ab" 区分）。


class ABRunRequest(BaseModel):
    """``POST /v1/eval/ab`` 请求。"""

    top_k: int = 10
    rewrite: Literal["off", "auto"] = "off"
    eval_set: str | None = None
    pairs: list[str] | None = None  # pair 名称子集（rerank/multipath_rrf/graph）；None=默认 3 组
    graph_subset: bool = False  # 对 call_chain 标签子集额外跑 graph pair
    diagnose: bool = False  # True → 详情返回向量路诊断字段（recall_paths/retrieved_kinds）
    persist: bool = True


class ABDelta(BaseModel):
    """单指标 baseline→treatment delta：abs=t−b；pct=(t−b)/b*100（b 为 None/0 → None）。"""

    abs: float | None = None
    pct: float | None = None


class ABPairResult(BaseModel):
    """一组 A/B 对照结果。``delta`` 形如 ``{recall:{1:{abs,pct},...}, mrr:{abs,pct}}``。"""

    name: str
    claim: str
    baseline: str
    treatment: str
    metric_focus: list[str]
    delta: dict[str, Any]


class ABVariantResult(BaseModel):
    """单个检索变体的聚合结果。per_query 仅在 detail（且 diagnose 时含诊断字段）出现。"""

    ablation: dict[str, Any]
    desc: str
    aggregate: EvalAggregate | None = None
    n_evaluable: int = 0
    n_queries: int = 0
    rerank_on_count: int = 0
    unresolved: int = 0
    per_query: list[dict[str, Any]] | None = None


class ABRunSummary(BaseModel):
    """A/B 历史列表项（不含 variants 的 per_query，保持列表轻量）。"""

    run_id: int
    status: str
    trigger: str
    top_k: int
    rewrite: str
    embedding_strategy: str
    n_queries: int
    n_evaluable: int
    rerank_on_count: int
    duration_ms: int | None
    pairs: list[ABPairResult]  # 各 pair 的 delta（轻量，列表即可看结论）
    aggregate: EvalAggregate | None = None  # full 变体锚点，供统一历史表趋势图
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    error_message: str | None = None
    kind: str = "ab"


class ABRunDetail(ABRunSummary):
    """A/B 详情（POST 运行结果 + GET /ab-runs/{id}）：附各变体明细 + 请求 config。"""

    variants: dict[str, ABVariantResult]
    config: dict[str, Any] | None = None


class ABRunListResponse(BaseModel):
    """``GET /v1/eval/ab-runs`` 响应。"""

    total: int
    items: list[ABRunSummary]


# ===== QA / 幻觉 eval（M39；config.kind="qa"，复用 eval_runs，零迁移）=====


class QARunRequest(BaseModel):
    """``POST /v1/eval/qa`` 请求。"""

    top_k: int = 8
    rewrite: Literal["off", "auto"] = "off"
    eval_set: str | None = None  # 缺省用 backend/eval/eval_set_qa.yaml
    persist: bool = True


class QADimensionScore(BaseModel):
    score: float | None = None
    weight: float = 1.0


class QAPerQueryRow(BaseModel):
    """单条 QA query 评判结果。"""

    id: str
    text: str
    answer: str
    citations_n: int = 0
    unverified_rate: float | None = None
    judge_scores: dict[str, Any] = {}  # {dim: float|None}
    rationale: str = ""
    weighted_score: float | None = None
    error: str | None = None


class QAAggregate(BaseModel):
    """QA 宏平均：5 维均值（4 judge + unverified_rate）+ weighted_quality（仅 rubric 4 维）。"""

    n: int
    means: dict[str, float | None]
    weighted_quality: float | None = None


class QARunSummary(BaseModel):
    """QA 历史列表项（不含 per_query）。"""

    run_id: int
    status: str
    trigger: str
    top_k: int
    rewrite: str
    embedding_strategy: str
    n_queries: int
    n_evaluable: int
    duration_ms: int | None
    aggregate: QAAggregate | None = None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    error_message: str | None = None
    kind: str = "qa"


class QARunDetail(QARunSummary):
    """QA 详情：附 per_query + config。"""

    per_query: list[dict[str, Any]] | None = None
    config: dict[str, Any] | None = None


class QARunListResponse(BaseModel):
    total: int
    items: list[QARunSummary]
