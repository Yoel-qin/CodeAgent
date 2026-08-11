"""检索评测编排（横切·评测 / 后端设计 Phase 9.2）。

走**真实检索管线**（``pipeline.recall`` 全漏斗：向量 + BM25 + 图遍历 → RRF → 精排），
逐 query 度量 Recall@K / MRR / NDCG，宏平均聚合。纯读、零运行时改动。

ground-truth 以**人工可读标注**表达，``resolve_relevant`` 运行时解析为 chunk_id 集合（对内容变更鲁棒，
因为 ``chunk_id`` 内嵌 ``sha256(content)[:8]`` 会随内容变）：
- 含 ``.`` → ``code_anchor_key``（``"Account.deposit"``，见 ``pipeline/metadata.make_anchor_key``）
- 以 ``code_``/``doc_`` 开头 → literal ``chunk_id``
- 否则 → ``class_name``（整文件/类级 chunk，如 ``"Foo"``；其 ``code_anchor_key`` 为 None）

默认 ``rewrite="off"``：传 ``semantic_query=query, terms=extract_query_terms(query), rewritten=False``
绕过 Stage-0 LLM 改写（``pipeline.recall`` 仅在 ``semantic_query is None`` 时改写），使检索漏斗**内禀质量**
可复现度量、A/B delta 可归因。``rewrite="auto"`` 走生产全链路（含 LLM 改写，需 key、非确定）。

``recall_fn`` 可注入（DI 接缝，测试用假函数，免真实 Milvus/ES）。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.eval import metrics
from app.retrieval.pipeline import pipeline
from app.retrieval.query_understanding import extract_query_terms

logger = logging.getLogger(__name__)


@dataclass
class EvalQuery:
    """评测集单条：query 文本 + relevant 标注（anchor/类名/chunk_id 混用）。

    ``tags``：可选子集标签（如 ``call_chain``），供 A/B 评测按子集过滤
    （见 ``ab_service`` 的图遍历调用链子集）。默认空 → 不影响普通评测。
    """

    id: str
    text: str
    relevant: list[str]
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    config: dict
    aggregate: dict
    n_queries: int
    n_evaluable: int
    rerank_on_count: int
    per_query: list[dict]
    unresolved: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


def load_eval_queries(path: str) -> list[EvalQuery]:
    """从 ``.yaml``/``.yml``/``.json`` 评测集加载 query 列表（含可选 ``tags``）。

    pyyaml 为传递依赖（pydantic-settings/langchain）；缺失时改用 ``.json`` 评测集。
    """
    with open(path, encoding="utf-8") as f:
        if path.endswith((".yaml", ".yml")):
            import yaml  # pyyaml 传递依赖；无则改用 .json

            data = yaml.safe_load(f)
        else:
            data = json.load(f)
    return [
        EvalQuery(
            id=str(q["id"]),
            text=str(q["text"]),
            relevant=list(q.get("relevant", [])),
            tags=[str(t) for t in q.get("tags", [])],
        )
        for q in data.get("queries", [])
    ]


def _classify(entries: Sequence[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    """把 relevant 标注按解析方式分四类：anchor / code 字面 chunk_id / doc 字面 chunk_id / 类名。"""
    anchors, lit_code, lit_doc, classes = [], [], [], []
    for e in entries:
        if "." in e:
            anchors.append(e)
        elif e.startswith("code_"):
            lit_code.append(e)
        elif e.startswith("doc_"):
            lit_doc.append(e)
        else:
            classes.append(e)
    return anchors, lit_code, lit_doc, classes


async def _resolve_code_anchors(session: AsyncSession, anchors: list[str]) -> tuple[set[str], set[str]]:
    """返回 (命中 chunk_id 集, 命中的 anchor 集)。"""
    if not anchors:
        return set(), set()
    rows = (await session.execute(text(
        "SELECT chunk_id, code_anchor_key FROM code_chunks "
        "WHERE code_anchor_key = ANY(:a) AND is_deleted = false"
    ), {"a": anchors})).mappings().all()
    ids = {r["chunk_id"] for r in rows}
    matched = {r["code_anchor_key"] for r in rows}
    return ids, matched


async def _resolve_code_classes(session: AsyncSession, classes: list[str]) -> tuple[set[str], set[str]]:
    """类名解析（整文件/类级 chunk，如 Foo）。返回 (chunk_id 集, 命中类名集)。"""
    if not classes:
        return set(), set()
    rows = (await session.execute(text(
        "SELECT chunk_id, class_name FROM code_chunks "
        "WHERE class_name = ANY(:c) AND is_deleted = false"
    ), {"c": classes})).mappings().all()
    ids = {r["chunk_id"] for r in rows}
    matched = {r["class_name"] for r in rows}
    return ids, matched


async def _resolve_literals(
    session: AsyncSession, code_ids: list[str], doc_ids: list[str]
) -> set[str]:
    """字面 chunk_id 解析（code_ → code_chunks，doc_ → doc_chunks）。"""
    out: set[str] = set()
    if code_ids:
        rows = (await session.execute(text(
            "SELECT chunk_id FROM code_chunks WHERE chunk_id = ANY(:c) AND is_deleted = false"
        ), {"c": code_ids})).mappings().all()
        out.update(r["chunk_id"] for r in rows)
    if doc_ids:
        rows = (await session.execute(text(
            "SELECT chunk_id FROM doc_chunks WHERE chunk_id = ANY(:c) AND is_deleted = false"
        ), {"c": doc_ids})).mappings().all()
        out.update(r["chunk_id"] for r in rows)
    return out


async def resolve_relevant(session: AsyncSession, entries: Sequence[str]) -> tuple[set[str], list[str]]:
    """解析 relevant 标注 → (chunk_id 集, 未命中标注列表)。"""
    anchors, lit_code, lit_doc, classes = _classify(entries)
    resolved: set[str] = set()
    missing: list[str] = []

    a_ids, a_matched = await _resolve_code_anchors(session, anchors)
    resolved |= a_ids
    missing += [a for a in anchors if a not in a_matched]

    c_ids, c_matched = await _resolve_code_classes(session, classes)
    resolved |= c_ids
    missing += [c for c in classes if c not in c_matched]

    lit_ids = await _resolve_literals(session, lit_code, lit_doc)
    resolved |= lit_ids
    missing += [c for c in (lit_code + lit_doc) if c not in lit_ids]

    return resolved, missing


async def run_eval(
    session: AsyncSession,
    queries: Sequence[EvalQuery],
    *,
    top_k: int = 10,
    rewrite: str = "off",
    recall_fn=None,
) -> EvalReport:
    """逐 query 解析 ground-truth → 召回 → 度量 → 聚合。

    - relevant 解析为空 → 记 ``unresolved`` 并跳过（不计入聚合）。
    - 单 query 召回异常 → 记 ``error``、retrieved=[]（计入聚合为 0 分，不中断）。
    """
    recall = recall_fn if recall_fn is not None else pipeline.recall
    per_query: list[dict] = []
    unresolved: list[dict] = []
    rerank_on_count = 0

    for q in queries:
        resolved, missing = await resolve_relevant(session, q.relevant)
        if not resolved:
            unresolved.append({"id": q.id, "text": q.text, "missing": list(q.relevant)})
            continue

        kw = (
            dict(semantic_query=q.text, terms=extract_query_terms(q.text), rewritten=False)
            if rewrite == "off" else {}
        )
        try:
            cands, meta = await recall(session, q.text, top_k=top_k, **kw)
            retrieved = [c["chunk_id"] for c in cands]
            retrieved_kinds = [c.get("kind") for c in cands]
            recall_paths = meta.get("recall_paths")
            rerank_on = bool(meta.get("rerank_on"))
            error = None
        except Exception as e:  # 单 query 失败不中断整轮评测
            logger.warning("eval query %s recall failed: %s", q.id, e)
            retrieved, retrieved_kinds, recall_paths, rerank_on, error = (
                [], [], None, False, f"{type(e).__name__}: {e}",
            )

        if rerank_on:
            rerank_on_count += 1
        result = metrics.evaluate_query(retrieved, resolved)
        per_query.append({
            "id": q.id,
            "text": q.text,
            "relevant": sorted(resolved),
            "retrieved": retrieved,
            # M25 诊断：每候选 kind + 三路候选投影，供 A/B 定位向量路漏召模式（None-safe：注入的
            # recall_fn 不发 recall_paths 时为 None）。
            "retrieved_kinds": retrieved_kinds,
            "recall_paths": recall_paths,
            "missing": missing,
            "rerank_on": rerank_on,
            "error": error,
            **result,
        })

    agg = metrics.aggregate(per_query)
    return EvalReport(
        config={"top_k": top_k, "rewrite": rewrite},
        aggregate=agg,
        n_queries=len(queries),
        n_evaluable=len(per_query),
        rerank_on_count=rerank_on_count,
        per_query=per_query,
        unresolved=unresolved,
    )
