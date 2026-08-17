"""文档问答 Agent 的工具集（3 个，对齐设计 §15.2）。

结构与 ``code_tools.py`` 同构：
  - **工具逻辑**（``_search_docs`` 等）是纯 async 函数，取 ``session``，返回 ``ToolResult``，易单测；
  - **@tool 包装**取 ``config: RunnableConfig``（LangGraph 自动注入），从中拿 session/top_k，
    调逻辑函数，**经 get_stream_writer 推 ``agent_step`` + 逐条 ``citation``**，返回给 LLM 的**字符串观察**。

与 code_tools 的差异：``_norm`` 默认 ``kind="doc"``；``_search_docs`` 复用 ``pipeline.recall``
（返回 code+doc 混合池）后按 ``kind=="doc"`` 过滤（池取较大值避免 doc 饥饿）。

复用：``pipeline.recall``、``graph_service.get_code_doc_relations``（双向、doc 可种子，找关联代码佐证）、
``fetch_chunks``、``chat_service._citation``。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.graph_traverse import fetch_chunks
from app.retrieval.pipeline import pipeline
from app.retrieval.query_understanding import extract_query_terms
from app.services import graph_service
from app.services.chat_service import _citation

from . import formatting as fmt


@dataclass
class ToolResult:
    """工具返回：text=给 LLM 的观察文本；chunks=供引用累积的候选（已归一，含 score/kind）。"""

    text: str
    chunks: list[dict] = field(default_factory=list)


# ---- 归一化 / 事件 helper ----


def _norm(c: dict, *, score: float | None = None) -> dict:
    """确保候选 dict 有 score(float) 与 kind（文档工具默认 doc），满足 chat_service._citation。"""
    d = dict(c)
    d.setdefault("kind", "doc")
    if score is not None:
        d["score"] = score
    try:
        d["score"] = float(d.get("score") or 0.0)
    except (TypeError, ValueError):
        d["score"] = 0.0
    return d


def _safe_writer():
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001
        return None


def _emit_step(name: str, args: dict, n: int, duration_ms: float | None = None) -> None:
    # M41：duration_ms 不为 None 时加入 data（旧前端缺省不受影响）
    if (w := _safe_writer()) is None:
        return
    try:
        data: dict = {"tool": name, "args": args, "n": n}
        if duration_ms is not None:
            data["duration_ms"] = round(duration_ms, 2)
        w({"event": "agent_step", "data": data})
    except Exception:  # noqa: BLE001
        pass


def _emit_citations(chunks: list[dict]) -> None:
    if (w := _safe_writer()) is None:
        return
    for c in chunks:
        try:
            w({"event": "citation", "data": _citation(c)})
        except Exception:  # noqa: BLE001
            pass


# ---- 工具逻辑（纯函数，可单测）----


async def _search_docs(query: str, session: AsyncSession, *, top_k: int = 8, pool: int = 15) -> ToolResult:
    """recall 返回 code+doc 混合池，按 kind=="doc" 过滤后切到 top_k。"""
    ranked, meta = await pipeline.recall(session, query, top_k=pool)
    docs = [_norm(c) for c in ranked if c.get("kind") == "doc"][:top_k]
    recall = meta.get("recall", {})
    note = (f"（文档池 {len(docs)} 段；漏斗 向量 {recall.get('vector', 0)} + 词法 {recall.get('lexical', 0)} "
            f"+ 图 {recall.get('graph', 0)} → 精排 {meta.get('fine', 0)}）")
    return ToolResult(f"{fmt.format_doc_candidates(docs)}\n{note}", docs)


async def _read_doc(chunk_id: str, session: AsyncSession) -> ToolResult:
    """读取某文档段落的全文 + 章节面包屑 + 类型（表格/图片）+ 文件出处。"""
    sql = sql_text("""SELECT dc.chunk_id, dc.content, dc.heading_path, dc.heading_level, dc.section_order,
           dc.chunk_content_type, dc.page_number, dc.image_description, dc.image_caption,
           dc.table_data, dc.table_description, dc.table_total_rows, dc.table_total_cols,
           dc.context_before, dc.context_after, dc.linked_code_ids, dc.keywords,
           df.file_path, df.title, df.doc_type
       FROM doc_chunks dc LEFT JOIN doc_files df ON dc.file_id = df.file_id
       WHERE dc.chunk_id = :id AND dc.is_deleted = false""")
    row = (await session.execute(sql, {"id": chunk_id})).mappings().first()
    if row is None:
        return ToolResult(f"（未找到 chunk_id={chunk_id} 的文档）", [])
    d = dict(row)
    chunks = [_norm({
        "chunk_id": d["chunk_id"], "kind": "doc", "content": d["content"],
        "heading_path": d.get("heading_path"),
    }, score=1.0)]
    return ToolResult(fmt.format_doc_detail(d), chunks)


async def _get_related_code(center: str, session: AsyncSession, *, depth: int = 1) -> ToolResult:
    """从文档段（center=doc chunk_id）出发，经 DOC_TO_CODE/CODE_TO_DOC 找关联代码作佐证。"""
    resp = await graph_service.get_code_doc_relations(session, center, depth=depth, max_nodes=30)
    # 只取代码 chunk_id（排除 doc 中心与 class:X 概念节点，fetch_chunks 按 chunk_id 取内容）
    ids = [n.id for n in resp.nodes if not str(n.id).startswith(("class:", "doc_"))]
    raw = await fetch_chunks(session, ids)
    chunks = [_norm(c, score=0.5) for c in raw]
    return ToolResult(fmt.format_related_code(resp), chunks)


async def _search_media(query: str, session: AsyncSession, *, media_type: str, top_k: int = 8) -> ToolResult:
    """按描述检索图片/表格文档段（media_type='image'/'table'）。

    image → ``image_description``，table → ``table_description``。命中：描述 ILIKE query 子串，
    **或** keywords 与 query 分词（``extract_query_terms``）重叠。按关键词重叠数打分（ILIKE 命中
    给基础分 0.3）。区别于 ``_search_docs``（按 content 检索全 doc 池）——本工具专攻带描述的媒体段，
    能找回 content 为空但描述详尽的图/表。返回 doc chunks（可引用）。
    """
    terms = [t.lower() for t in extract_query_terms(query)]
    desc_col = "image_description" if media_type == "image" else "table_description"
    sql = sql_text(f"""
        SELECT chunk_id, content, heading_path, keywords, {desc_col} AS description
        FROM doc_chunks
        WHERE is_deleted = false AND chunk_content_type = :mt
          AND ({desc_col} ILIKE :q OR keywords ?| cast(:terms as text[]))
    """)
    rows = (await session.execute(sql, {
        "mt": media_type, "q": f"%{query}%", "terms": list({*terms, query.lower()}),
    })).mappings().all()
    scored = []
    for r in rows:
        kws = {k.lower() for k in (r["keywords"] or [])}
        score = max(0.3, float(len(kws & set(terms))))
        scored.append((score, dict(r)))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]
    chunks = [_norm({
        "chunk_id": r["chunk_id"], "kind": "doc",
        "content": r.get("description") or r.get("content") or "",
        "heading_path": r.get("heading_path") or [],
    }, score=s) for s, r in top]
    return ToolResult(fmt.format_media_search([r for _, r in top], media_type), chunks)


# ---- @tool 包装（供 create_react_agent 绑定；config 注入 session/top_k）----


@tool
async def search_docs(query: str, config: RunnableConfig) -> str:
    """在文档库中检索与问题相关的文档段落（设计文档/配置/概念说明）。语义+词法+图+精排。
    用于回答配置、概念、用法类问题。返回文档段落列表（含 chunk_id 与章节）。"""

    session: AsyncSession = config["configurable"]["session"]
    top_k = config["configurable"].get("top_k", 8)
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _search_docs(query, session, top_k=top_k)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "search_docs", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"query": query}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("search_docs", {"query": query}, len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def read_doc(chunk_id: str, config: RunnableConfig) -> str:
    """读取某个文档段落的完整内容 + 章节面包屑 + 类型（表格/图片）+ 文件出处。chunk_id 来自
    search_docs 的返回。用于精读某段文档的完整说明。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _read_doc(chunk_id, session)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "read_doc", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"chunk_id": chunk_id}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("read_doc", {"chunk_id": chunk_id}, len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def get_related_code(center: str, config: RunnableConfig) -> str:
    """查找与某个文档段落关联的代码实现（作佐证）。center 是文档段的 chunk_id（doc_ 前缀）。
    用于回答"文档描述的配置/逻辑在代码里是怎么实现的"，增强可信度。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _get_related_code(center, session)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "get_related_code", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"center": center}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("get_related_code", {"center": center}, len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def image_search(query: str, config: RunnableConfig) -> str:
    """在文档库中按描述检索图片文档段（架构图/流程图/示意图/截图等）。当用户问"某张图/示意图/
    流程图"或想看可视化说明时用本工具（普通文字检索用 search_docs）。返回图片文档段列表
    （含 chunk_id 与图片描述）。"""

    session: AsyncSession = config["configurable"]["session"]
    top_k = config["configurable"].get("top_k", 8)
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _search_media(query, session, media_type="image", top_k=top_k)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "image_search", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"query": query}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("image_search", {"query": query}, len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def table_search(query: str, config: RunnableConfig) -> str:
    """在文档库中按描述检索表格文档段（配置表/参数表/映射表/对照表等）。当用户问"某张表/
    参数表/对照表"或想要结构化数据时用本工具（普通文字检索用 search_docs）。返回表格文档段
    列表（含 chunk_id 与表格描述）。"""

    session: AsyncSession = config["configurable"]["session"]
    top_k = config["configurable"].get("top_k", 8)
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _search_media(query, session, media_type="table", top_k=top_k)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "table_search", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"query": query}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("table_search", {"query": query}, len(res.chunks), duration_ms=_dur)
    return res.text


#: 文档问答 Agent 绑定的工具集
DOC_TOOLS = [search_docs, read_doc, get_related_code, image_search, table_search]
