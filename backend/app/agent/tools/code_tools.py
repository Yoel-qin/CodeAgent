"""代码理解 Agent 的工具集（5 个，对齐设计 §14.4）。

设计要点：
  - **工具逻辑**（``_search_code`` 等）是纯 async 函数，取 ``session``，返回 ``ToolResult``，易单测。
  - **@tool 包装**取 ``config: RunnableConfig``（LangGraph 自动注入），从中拿 session/top_k，
    调逻辑函数，**经 get_stream_writer 推 ``agent_step`` + 逐条 ``citation`` 事件**（适配器据此累积引用），
    返回给 LLM 的**字符串观察**（标准模式：LLM 能看到工具结果）。
  - 不用 Command / 不写图 state —— 引用经事件流累积，鲁棒且无版本摩擦。

复用：``pipeline.recall``、``graph_service.{get_call_graph,get_code_doc_relations,search_graph_nodes}``、
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

from app.core.config import settings
from app.retrieval.graph_traverse import fetch_chunks
from app.retrieval.pipeline import pipeline
from app.retrieval.reranker import rerank_stage
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
    """确保候选 dict 有 score(float) 与 kind，满足 chat_service._citation 要求。"""
    d = dict(c)
    d.setdefault("kind", "code")
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


async def _search_code(query: str, session: AsyncSession, *, top_k: int = 8) -> ToolResult:
    ranked, meta = await pipeline.recall(session, query, top_k=top_k)
    chunks = [_norm(c) for c in ranked]
    recall = meta.get("recall", {})
    note = (f"（向量 {recall.get('vector', 0)} + 词法 {recall.get('lexical', 0)} "
            f"+ 图 {recall.get('graph', 0)} → 精排 {meta.get('fine', len(chunks))}）")
    return ToolResult(f"{fmt.format_code_candidates(chunks)}\n{note}", chunks)


async def _search_symbol(q: str, session: AsyncSession) -> ToolResult:
    resp = await graph_service.search_graph_nodes(session, q, limit=10)
    chunks = [
        _norm({"chunk_id": it.id, "kind": "code", "content": it.name,
               "class_name": it.class_name, "method_name": it.name}, score=0.5)
        for it in resp.items if it.type == "method"
    ]
    return ToolResult(fmt.format_symbol_search(resp.items), chunks)


async def _get_call_chain(center: str, direction: str, session: AsyncSession, *,
                          depth: int = 2) -> ToolResult:
    resp = await graph_service.get_call_graph(
        session, center, depth=depth, direction=direction or "BOTH", max_nodes=30,
    )
    ids = [n.id for n in resp.nodes if not str(n.id).startswith("class:")]
    raw = await fetch_chunks(session, ids)
    depth_of = {n.id: (n.depth or 0) for n in resp.nodes}
    chunks = [_norm(c, score=max(0.1, 1.0 - 0.15 * depth_of.get(c["chunk_id"], 0))) for c in raw]
    return ToolResult(fmt.format_call_graph(resp, direction or "BOTH"), chunks)


async def _get_callers(center: str, session: AsyncSession, *, depth: int = 3) -> ToolResult:
    """上游影响面：谁调用了 center（变更影响的爆炸半径）。

    与 ``_get_call_chain`` 同构，但方向锁 CALLERS、``max_nodes`` 放到 120（``get_call_chain`` 的 30
    对广泛被调方法会截断爆炸半径）。供变更影响 Agent 调用。
    """
    resp = await graph_service.get_call_graph(
        session, center, depth=depth, direction="CALLERS", max_nodes=120,
    )
    ids = [n.id for n in resp.nodes if not str(n.id).startswith("class:")]
    raw = await fetch_chunks(session, ids)
    depth_of = {n.id: (n.depth or 0) for n in resp.nodes}
    chunks = [_norm(c, score=max(0.1, 1.0 - 0.15 * depth_of.get(c["chunk_id"], 0))) for c in raw]
    note = f"（{len(chunks)} 个节点，direction=CALLERS，depth={depth}）"
    return ToolResult(f"{fmt.format_impact_callers(resp)}\n{note}", chunks)


async def _get_related_docs(center: str, session: AsyncSession, *, depth: int = 1) -> ToolResult:
    resp = await graph_service.get_code_doc_relations(session, center, depth=depth, max_nodes=30)
    ids = [n.id for n in resp.nodes]
    raw = await fetch_chunks(session, ids)
    chunks = [_norm(c, score=0.5) for c in raw]
    return ToolResult(fmt.format_related_docs(resp), chunks)


async def _read_code(chunk_id: str, session: AsyncSession) -> ToolResult:
    sql = sql_text("""SELECT cc.chunk_id, cc.content, cc.class_name, cc.method_name, cc.method_signature,
           cc.start_line, cc.end_line, cc.javadoc, cf.file_path
       FROM code_chunks cc LEFT JOIN code_files cf ON cc.file_id = cf.file_id
       WHERE cc.chunk_id = :id AND cc.is_deleted = false""")
    row = (await session.execute(sql, {"id": chunk_id})).mappings().first()
    if row is None:
        return ToolResult(f"（未找到 chunk_id={chunk_id} 的代码）", [])
    d = dict(row)
    chunks = [_norm({
        "chunk_id": d["chunk_id"], "kind": "code", "content": d["content"],
        "class_name": d["class_name"], "method_name": d["method_name"],
    }, score=1.0)]
    return ToolResult(fmt.format_code_detail(d), chunks)


async def _get_recent_changes(chunk_id: str, session: AsyncSession) -> list[dict]:
    """查 chunk 最近的 git 变更记录（change_history 表，按提交时间倒序，最多 10 条）。

    供缺陷诊断 Agent 做回归排查（"这段代码最近改过吗？是不是引入了 bug"）。**元数据工具**：
    无 chunks（不发 citation，避免与 read_code 重复），故返回原始行（dict 列表）由 @tool 包装
    格式化。只有增量同步过的代码才有历史——全量入库为空，调用方据此降级。
    """
    sql = sql_text(
        "SELECT change_type, git_commit_hash, git_commit_time, git_author, commit_message "
        "FROM change_history WHERE chunk_id = :cid "
        "ORDER BY git_commit_time DESC NULLS LAST LIMIT 10"
    )
    return [dict(m) for m in (await session.execute(sql, {"cid": chunk_id})).mappings().all()]


async def _get_metrics(chunk_id: str, session: AsyncSession) -> dict:
    """代码度量：LOC/token_count（code_chunks）+ fan-in/fan-out（call_graph 条件计数）。

    供代码审查 Agent 引用客观数字（复杂度 / 影响面）。**元数据工具**：无 chunks（不发 citation，
    避免与 read_code 重复）。缺失 chunk → ``{"found": False}``。仅用永远存在的整数列，不依赖可选的
    graph_embeddings（GNN 已弃用、未必有数据）。
    """
    sql = sql_text(
        "SELECT cc.chunk_id, cc.class_name, cc.method_name, cc.method_signature, "
        "cc.start_line, cc.end_line, cc.token_count FROM code_chunks cc "
        "WHERE cc.chunk_id = :id AND cc.is_deleted = false"
    )
    row = (await session.execute(sql, {"id": chunk_id})).mappings().first()
    if row is None:
        return {"found": False, "chunk_id": chunk_id}
    d = dict(row)
    cnt = sql_text(
        "SELECT COUNT(*) FILTER (WHERE callee_chunk_id = :id) AS fan_in, "
        "COUNT(*) FILTER (WHERE caller_chunk_id = :id) AS fan_out "
        "FROM call_graph WHERE is_deleted = false "
        "AND (caller_chunk_id = :id OR callee_chunk_id = :id)"
    )
    cres = (await session.execute(cnt, {"id": chunk_id})).mappings().first()
    return {
        "found": True,
        "chunk_id": d["chunk_id"],
        "class_name": d.get("class_name"),
        "method_name": d.get("method_name"),
        "method_signature": d.get("method_signature"),
        "loc": (d.get("end_line") or 0) - (d.get("start_line") or 0) + 1,
        "token_count": d.get("token_count"),
        "fan_in": int((cres or {}).get("fan_in") or 0),
        "fan_out": int((cres or {}).get("fan_out") or 0),
    }


async def _get_existing_tests(center: str, session: AsyncSession) -> tuple[list[dict], str]:
    """查找某类的现有测试类/方法（供测试生成 Agent 对齐项目测试约定）。

    解析 ``center`` → ``class_name``：``class:`` 前缀剥掉；是 chunk_id 则查 ``code_chunks.class_name``；
    否则原样当类名。再按 ``class_name ILIKE '{Class}%Test'``（如 ``Account%Test`` → AccountTest/AccountServiceTest）
    找测试 chunk，最多 8 条。前缀锚定使 ILIKE 可走 ``idx_code_chunks_class`` 索引、且精确不误吞。
    **内容工具**：返回真实可引用的测试 chunk（由 @tool 发 citation），区别于元数据工具。空列表 = 无现有测试。
    """
    if center.startswith("class:"):
        cls = center[len("class:"):]
    else:
        # 先当 chunk_id 解析 class_name；查不到（如直接传了类名）则把 center 当类名
        row = (await session.execute(
            sql_text("SELECT class_name FROM code_chunks WHERE chunk_id = :id AND is_deleted = false"),
            {"id": center},
        )).mappings().first()
        cls = (row or {}).get("class_name") or center
    sql = sql_text(
        "SELECT cc.chunk_id, cc.class_name, cc.method_name, cc.method_signature, cc.content, "
        "cf.file_path FROM code_chunks cc LEFT JOIN code_files cf ON cc.file_id = cf.file_id "
        "WHERE cc.is_deleted = false AND cc.class_name ILIKE :pat "
        "ORDER BY cc.class_name, cc.start_line LIMIT 8"
    )
    rows = (await session.execute(sql, {"pat": f"{cls}%Test"})).mappings().all()
    return [dict(m) for m in rows], cls


async def _get_downstream_callers(center: str, session: AsyncSession, *, depth: int = 3) -> ToolResult:
    """下游被调用面：center 调用了谁（它依赖什么）。

    与 ``_get_callers`` 同构（方向锁 CALLEES、max_nodes=120、depth=3），但语义为「它调用了什么」
    ——既有 ``get_callers`` 是 CALLERS 上游影响面，本工具补全下游依赖视角，供变更影响 Agent。
    """
    resp = await graph_service.get_call_graph(
        session, center, depth=depth, direction="CALLEES", max_nodes=120,
    )
    ids = [n.id for n in resp.nodes if not str(n.id).startswith("class:")]
    raw = await fetch_chunks(session, ids)
    depth_of = {n.id: (n.depth or 0) for n in resp.nodes}
    chunks = [_norm(c, score=max(0.1, 1.0 - 0.15 * depth_of.get(c["chunk_id"], 0))) for c in raw]
    note = f"（{len(chunks)} 个节点，direction=CALLEES，depth={depth}）"
    return ToolResult(f"{fmt.format_impact_callees(resp)}\n{note}", chunks)


async def _get_affected_docs(center: str, session: AsyncSession) -> ToolResult:
    """查找锚定到 center 代码的文档段（改该代码可能需同步更新这些文档）。

    center 解析为 code chunk_id 集合：``class:`` 前缀→该类全部 chunk；否则当 chunk_id。再查
    ``doc_chunks.linked_code_ids``（JSONB 数组）与该集合重叠、未删的文档段。best-effort 取代码
    最近变更（``change_history``）作腐化信号挂到每条文档（对接文档维护弧线）。**内容工具**：
    返回可引用的 doc chunks。
    """
    # 1) 解析 center → code chunk_ids
    if center.startswith("class:"):
        cls = center[len("class:"):]
        id_rows = (await session.execute(sql_text(
            "SELECT chunk_id FROM code_chunks WHERE is_deleted = false AND class_name = :cls"
        ), {"cls": cls})).mappings().all()
        ids = [r["chunk_id"] for r in id_rows]
    else:
        ids = [center]
    if not ids:
        return ToolResult(f"（未解析出 {center} 对应的代码 chunk）", [])
    # 2) doc_chunks.linked_code_ids 重叠
    sql = sql_text("""
        SELECT dc.chunk_id, dc.content, dc.heading_path
        FROM doc_chunks dc
        WHERE dc.is_deleted = false AND dc.linked_code_ids ?| cast(:ids as text[])
        ORDER BY dc.section_order NULLS LAST LIMIT 15
    """)
    rows = [dict(m) for m in (await session.execute(sql, {"ids": ids})).mappings().all()]
    # 3) best-effort 代码最近变更（腐化信号）——代码侧属性，所有文档共享同一条
    last_change = None
    lc = (await session.execute(sql_text(
        "SELECT chunk_id, change_type, git_commit_time, commit_message FROM change_history "
        "WHERE chunk_id = ANY(:ids) ORDER BY git_commit_time DESC NULLS LAST LIMIT 1"
    ), {"ids": ids})).mappings().all()
    if lc:
        last_change = dict(lc[0])
    for r in rows:
        r["last_change"] = last_change
    chunks = [_norm({
        "chunk_id": r["chunk_id"], "kind": "doc", "content": r.get("content"),
        "heading_path": r.get("heading_path") or [],
    }, score=0.5) for r in rows]
    return ToolResult(fmt.format_affected_docs(rows, center), chunks)


async def _rerank(query: str, chunk_ids: list[str], session: AsyncSession) -> tuple[list[dict], bool]:
    """对给定候选 chunk_ids 用精排模型按 query 重排。返回 (重排后候选, 是否真正重排)。

    无候选 → ([], False)；候选经 ``fetch_chunks`` 水合后送 ``rerank_stage``（按 content 打分）。
    无模型/无 key/异常 → 降级原序、``reranked=False``（不抛错，由 @tool 降级提示）。
    """
    if not chunk_ids:
        return [], False
    raw = await fetch_chunks(session, list(chunk_ids))
    candidates = [_norm(c) for c in raw]
    if not candidates:
        return [], False
    model = settings.reranker_fine_model
    if not model:
        return candidates, False
    try:
        ranked = await rerank_stage(query, candidates, model=model, top_n=len(candidates))
        return ranked, True
    except Exception:  # noqa: BLE001  无 key / 网络错 → 降级原序
        return candidates, False


# ---- @tool 包装（供 create_react_agent 绑定；config 注入 session/top_k）----


@tool
async def search_code(query: str, config: RunnableConfig) -> str:
    """在代码库中检索与问题相关的代码片段（方法/类）。语义+词法+图+精排。
    用于定位用户提到的类或方法。返回片段列表（含 chunk_id）。"""

    session: AsyncSession = config["configurable"]["session"]
    top_k = config["configurable"].get("top_k", 8)
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _search_code(query, session, top_k=top_k)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "search_code", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"query": query}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("search_code", {"query": query}, len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def search_symbol(q: str, config: RunnableConfig) -> str:
    """按名称查找类/方法，返回可作为其它工具 center 参数的 id（class:类名 或 chunk_id）。
    当用户只给了一个名字、还不确定具体 chunk 时，先用本工具解析。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _search_symbol(q, session)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "search_symbol", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"q": q}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("search_symbol", {"q": q}, len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def get_call_chain(center: str, config: RunnableConfig, direction: str = "BOTH",
                         depth: int = 2) -> str:
    """从某个方法/类出发展开调用链。center 是 chunk_id 或 class:类名；
    direction=CALLERS(谁调用它)/CALLEES(它调用谁)/BOTH(双向)；depth 为展开层数。
    用于回答"被谁调用 / 调用了什么 / 影响范围"。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _get_call_chain(center, direction, session, depth=depth)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "get_call_chain", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"center": center, "direction": direction, "depth": depth},
                                "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("get_call_chain", {"center": center, "direction": direction, "depth": depth},
               len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def get_callers(center: str, config: RunnableConfig, depth: int = 3) -> str:
    """查找"谁调用了 center"（上游影响面/爆炸半径），评估修改 center 会波及哪些代码。
    center 是 chunk_id 或 class:类名；depth 为展开层数（默认 3，比 get_call_chain 更深更全）。
    返回按层归类的受影响调用方。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _get_callers(center, session, depth=depth)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "get_callers", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"center": center, "depth": depth}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("get_callers", {"center": center, "depth": depth}, len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def get_related_docs(center: str, config: RunnableConfig) -> str:
    """查找与某个代码块关联的文档章节（设计/用法说明）。center 是 chunk_id 或 class:类名。
    用于回答"为什么这么设计 / 文档怎么描述这段代码"。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _get_related_docs(center, session)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "get_related_docs", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"center": center}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("get_related_docs", {"center": center}, len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def read_code(chunk_id: str, config: RunnableConfig) -> str:
    """读取某个代码片段的完整源码 + 签名 + Javadoc + 文件位置。chunk_id 来自 search_code/
    search_symbol/get_call_chain 的返回。用于精读某个方法的实现细节。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _read_code(chunk_id, session)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "read_code", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"chunk_id": chunk_id}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("read_code", {"chunk_id": chunk_id}, len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def get_recent_changes(chunk_id: str, config: RunnableConfig) -> str:
    """查询某个代码片段（方法/类）最近的 git 变更记录（类型/提交/作者/提交信息）。
    chunk_id 来自 search_code/search_symbol/get_call_chain。用于缺陷诊断的回归排查：
    判断该代码是否近期被改动、可能引入 bug。注意：只有经过增量同步的代码才有变更历史。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    rows = await _get_recent_changes(chunk_id, session)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "get_recent_changes", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"chunk_id": chunk_id}, "n": len(rows)})
    _emit_step("get_recent_changes", {"chunk_id": chunk_id}, len(rows), duration_ms=_dur)
    return fmt.format_change_history(rows, chunk_id)


@tool
async def get_code_metrics(chunk_id: str, config: RunnableConfig) -> str:
    """查询某个代码片段（方法/类）的度量：代码行数(LOC)、token 数、fan-in(被多少处调用)、
    fan-out(调用多少处)。用于代码审查时引用客观数字佐证复杂度与影响面判断。
    chunk_id 来自 search_code/search_symbol/get_call_chain。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    metrics = await _get_metrics(chunk_id, session)
    _dur = (time.perf_counter() - _t0) * 1000
    _n = 1 if metrics.get("found") else 0
    if collector is not None:
        collector.record("tool", "get_code_metrics", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"chunk_id": chunk_id}, "n": _n})
    _emit_step("get_code_metrics", {"chunk_id": chunk_id}, _n, duration_ms=_dur)
    return fmt.format_code_metrics(metrics)


@tool
async def get_existing_tests(center: str, config: RunnableConfig) -> str:
    """查找某个类的现有测试类/方法（JUnit），供生成测试时对齐项目的测试约定（框架/命名/断言/mock 风格）。
    center 是 chunk_id、class:类名 或类名（来自 search_symbol/search_code/read_code）。命中则返回测试片段
    （含 chunk_id，可引用）；未命中说明库中无该类的测试，将按 JUnit 5 + Mockito 通用约定生成。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    rows, cls = await _get_existing_tests(center, session)
    _dur = (time.perf_counter() - _t0) * 1000
    chunks = [_norm({
        "chunk_id": r["chunk_id"], "kind": "code", "content": r.get("content"),
        "class_name": r.get("class_name"), "method_name": r.get("method_name"),
    }, score=0.6) for r in rows]
    if collector is not None:
        collector.record("tool", "get_existing_tests", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"center": center}, "n": len(rows)})
    _emit_citations(chunks)
    _emit_step("get_existing_tests", {"center": center}, len(rows), duration_ms=_dur)
    return fmt.format_existing_tests(rows, cls)


@tool
async def get_downstream_callers(center: str, config: RunnableConfig, depth: int = 3) -> str:
    """查找"center 调用了谁"（下游依赖面），看它依赖哪些代码。center 是 chunk_id 或 class:类名；
    depth 为展开层数（默认 3）。与 get_callers（上游影响面）对称：get_callers=谁调用它（改它波及谁），
    本工具=它调用谁（它的依赖/实现细节）。返回按层归类的下游被调用方。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _get_downstream_callers(center, session, depth=depth)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "get_downstream_callers", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"center": center, "depth": depth}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("get_downstream_callers", {"center": center, "depth": depth}, len(res.chunks),
               duration_ms=_dur)
    return res.text


@tool
async def get_affected_docs(center: str, config: RunnableConfig) -> str:
    """查找锚定到某段代码的文档段落（修改该代码可能需要同步更新的文档）。center 是 chunk_id 或
    class:类名（来自 search_symbol/search_code/read_code）。用于变更影响分析：评估改这段代码
    会影响哪些文档（附代码最近变更作腐化信号）。返回文档段落列表（含 chunk_id，可引用）。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    res = await _get_affected_docs(center, session)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "get_affected_docs", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"center": center}, "n": len(res.chunks)})
    _emit_citations(res.chunks)
    _emit_step("get_affected_docs", {"center": center}, len(res.chunks), duration_ms=_dur)
    return res.text


@tool
async def rerank(query: str, chunk_ids: list[str], config: RunnableConfig) -> str:
    """对一组候选代码/文档片段按与 query 的相关性用精排模型重排。query 为当前问题，chunk_ids 是
    先前 search_code/search_docs/get_call_chain 等返回的 chunk_id 列表。用于候选较多时聚焦最相关
    的若干条。注意：需配置精排模型；未启用或失败时保持原序（不报错）。"""

    session: AsyncSession = config["configurable"]["session"]
    collector = config["configurable"].get("trace")  # M41
    _t0 = time.perf_counter()
    ranked, reranked = await _rerank(query, chunk_ids, session)
    _dur = (time.perf_counter() - _t0) * 1000
    if collector is not None:
        collector.record("tool", "rerank", _dur,
                         parent_id=collector.stack_top,
                         attrs={"args": {"query": query, "n": len(chunk_ids)}, "n": len(ranked)})
    _emit_step("rerank", {"query": query, "n": len(chunk_ids)}, len(ranked), duration_ms=_dur)
    if not ranked:
        return "（无候选可重排）"
    note = "（已按相关性重排）" if reranked else "（精排未启用或失败，保持原序）"
    return f"{fmt.format_rerank(ranked)}\n{note}"


#: 代码理解 Agent 绑定的工具集
TOOLS = [search_code, search_symbol, get_call_chain, get_related_docs, read_code]
