"""文档维护 Agent 的工具集（Phase 7 Milestone 13：文档维护完整 ReAct）。

两个工具，供 DOC_MAINTAIN 的 ReAct ``propose`` 节点里 ``create_react_agent`` 自主排查「哪些文档锚点
与代码脱节、该标记过时」：

  - ``detect_stale_docs``（**内容工具，发 citation**；§7.2「变更历史类」）：解析 center→code chunk，
    查 ``chunk_relations`` 的 DOC_TO_CODE/CODE_TO_DOC 锚点，交叉 ``change_history`` 取 code 侧最近变更
    作 staleness 证据，返回候选（含 ``relation_id`` 供下一步提交）。
  - ``submit_proposal``（**ReAct 终结控制工具，不发 citation**）：把 LLM 选定的 ``relation_id`` 列表在 DB
    校验后，经 ``get_stream_writer`` 推一条**内部协议事件** ``_PROPOSAL_EVENT``（``propose`` 节点在
    ``agent.astream(stream_mode="custom")`` 循环里捕获、**不桥接到 SSE**），从而把结构化提案（summary/anchors/
    reason）从 ReAct Agent 流回主图 ``confirm``/``apply`` 节点。复用与 ``agent_step``/``citation`` 完全相同、
    已被 ``_base`` 证明可靠的 custom 事件通道，零新机制。

结构与 ``code_tools.py``/``doc_tools.py`` 同构：纯 async 逻辑（取 session）+ @tool 包装（config 注入 session、
经 get_stream_writer 推事件、返回给 LLM 的字符串观察）。
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.graph_traverse import fetch_chunks
from app.services.chat_service import _citation

from . import formatting as fmt

#: propose 节点捕获的内部协议事件名（**不**桥接到主图 SSE——仅 doc_maintain.propose 消费）。
_PROPOSAL_EVENT = "_proposal_captured"


# ---- 归一化 / 事件 helper（与 code_tools/doc_tools 同构）----


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


def _emit_step(name: str, args: dict, n: int) -> None:
    if (w := _safe_writer()) is None:
        return
    try:
        w({"event": "agent_step", "data": {"tool": name, "args": args, "n": n}})
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


async def _resolve_code_ids(center: str, session: AsyncSession) -> list[str]:
    """解析 center → code chunk_id 列表。

    - ``class:Foo`` 前缀 / 裸类名 → 按 ``class_name`` 查最多 8 个 chunk；
    - chunk_id → 先查 code_chunks 确认存在，命中则原样返回。

    解析不到（center 既非已知 chunk 也非已知类）→ 空列表（上层降级为「无锚点」）。
    """
    if center.startswith("class:"):
        cls = center[len("class:"):]
        rows = (await session.execute(
            sql_text("SELECT chunk_id FROM code_chunks WHERE is_deleted=false AND class_name = :cls LIMIT 8"),
            {"cls": cls},
        )).mappings().all()
        return [r["chunk_id"] for r in rows]
    # 先当 chunk_id：命中则原样
    hit = (await session.execute(
        sql_text("SELECT chunk_id FROM code_chunks WHERE chunk_id = :id AND is_deleted=false"),
        {"id": center},
    )).mappings().first()
    if hit:
        return [center]
    # 否则当类名
    rows = (await session.execute(
        sql_text("SELECT chunk_id FROM code_chunks WHERE is_deleted=false AND class_name = :cls LIMIT 8"),
        {"cls": center},
    )).mappings().all()
    return [r["chunk_id"] for r in rows]


async def _detect_stale(center: str, session: AsyncSession) -> tuple[list[dict], list[dict]]:
    """查 center 的文档↔代码锚点候选 + staleness 证据。

    返回 ``(rows, chunks)``：
      - ``rows``：候选锚点 ``{relation_id, anchor_key, relation_type, code_chunk_id, code_label,
        doc_chunk_id, doc_heading, last_change}``，``last_change=None`` 表示 code 侧无变更记录
        （全量入库、未经增量同步）。
      - ``chunks``：供 citation 累积的 code+doc chunk（已 fetch 内容，归一含 kind/score）。
    """
    code_ids = await _resolve_code_ids(center, session)
    if not code_ids:
        return [], []

    rels = [dict(m) for m in (await session.execute(sql_text(
        "SELECT relation_id, anchor_key, source_chunk_id, target_chunk_id, relation_type, confidence "
        "FROM chunk_relations "
        "WHERE relation_type IN ('DOC_TO_CODE','CODE_TO_DOC') AND is_stale = false "
        "AND (source_chunk_id = ANY(cast(:ids as text[])) "
        "OR target_chunk_id = ANY(cast(:ids as text[]))) "
        "ORDER BY confidence DESC LIMIT 12"
    ), {"ids": code_ids})).mappings().all()]
    if not rels:
        return [], []

    # 由 relation_type 推 code 侧 / doc 侧 id
    # （DOC_TO_CODE: source=doc, target=code；CODE_TO_DOC: source=code, target=doc）
    code_side: set[str] = set()
    doc_side: set[str] = set()
    for r in rels:
        if r["relation_type"] == "DOC_TO_CODE":
            r["doc_chunk_id"], r["code_chunk_id"] = r["source_chunk_id"], r["target_chunk_id"]
        else:
            r["code_chunk_id"], r["doc_chunk_id"] = r["source_chunk_id"], r["target_chunk_id"]
        code_side.add(r["code_chunk_id"])
        doc_side.add(r["doc_chunk_id"])

    # code 标签（class.method）
    code_labels: dict[str, str] = {}
    if code_side:
        for r in (await session.execute(sql_text(
            "SELECT chunk_id, class_name, method_name FROM code_chunks "
            "WHERE chunk_id = ANY(cast(:ids as text[]))"
        ), {"ids": list(code_side)})).mappings().all():
            cls, meth = r["class_name"], r["method_name"]
            code_labels[r["chunk_id"]] = f"{cls}.{meth}" if (cls and meth) else (cls or r["chunk_id"])

    # doc 标签（heading_path 面包屑）
    doc_labels: dict[str, str] = {}
    if doc_side:
        for r in (await session.execute(sql_text(
            "SELECT chunk_id, heading_path FROM doc_chunks WHERE chunk_id = ANY(cast(:ids as text[]))"
        ), {"ids": list(doc_side)})).mappings().all():
            hp = r["heading_path"]
            doc_labels[r["chunk_id"]] = " › ".join(str(h) for h in hp) if hp else "(无章节)"

    # staleness 证据：code 侧最近一次变更（DISTINCT ON 取每 chunk 最新）
    last_changes: dict[str, dict] = {}
    if code_side:
        for r in (await session.execute(sql_text(
            "SELECT DISTINCT ON (chunk_id) chunk_id, change_type, git_commit_time, commit_message "
            "FROM change_history WHERE chunk_id = ANY(cast(:ids as text[])) "
            "ORDER BY chunk_id, git_commit_time DESC NULLS LAST"
        ), {"ids": list(code_side)})).mappings().all():
            last_changes[r["chunk_id"]] = dict(r)

    rows = [{
        "relation_id": r["relation_id"],
        "anchor_key": r["anchor_key"] or "",
        "relation_type": r["relation_type"],
        "code_chunk_id": r["code_chunk_id"],
        "code_label": code_labels.get(r["code_chunk_id"], r["code_chunk_id"]),
        "doc_chunk_id": r["doc_chunk_id"],
        "doc_heading": doc_labels.get(r["doc_chunk_id"], "(未知文档)"),
        "last_change": last_changes.get(r["code_chunk_id"]),
    } for r in rels]

    # citation：两侧 chunk 取内容（fetch_chunks 已分 code/doc 并设 kind）
    chunks = [_norm(c, score=0.6) for c in await fetch_chunks(session, list(code_side | doc_side))]
    return rows, chunks


async def _validate_anchors(relation_ids: list[int], session: AsyncSession) -> list[dict]:
    """DB 侧校验 LLM 提交的 relation_id 列表：须存在、为 DOC_TO_CODE/CODE_TO_DOC、且未过时。

    过滤掉无效/已过时/非文档-代码的 id；返回校验通过的锚点行（含 relation_id/anchor_key/两端 chunk_id/
    relation_type，供 apply 节点写库）。空入参或全无效 → 空列表。
    """
    if not relation_ids:
        return []
    try:
        ids = list({int(x) for x in relation_ids})
    except (TypeError, ValueError):
        return []
    rows = (await session.execute(sql_text(
        "SELECT relation_id, anchor_key, source_chunk_id, target_chunk_id, relation_type "
        "FROM chunk_relations "
        "WHERE relation_id = ANY(cast(:ids as bigint[])) "
        "AND relation_type IN ('DOC_TO_CODE','CODE_TO_DOC') AND is_stale = false"
    ), {"ids": ids})).mappings().all()
    return [dict(m) for m in rows]


# ---- @tool 包装（供 create_react_agent 绑定；config 注入 session）----


@tool
async def detect_stale_docs(center: str, config: RunnableConfig) -> str:
    """查找与某段代码关联的文档锚点（chunk_relations 的 DOC_TO_CODE/CODE_TO_DOC），并附 staleness 证据
    （代码侧最近 git 变更），供判断文档是否过时。center 是 chunk_id、class:类名 或类名（来自 search_symbol/
    search_code/read_code）。返回锚点列表，**每条含 relation_id（提交提案时必须用这个 id）**。"""

    session: AsyncSession = config["configurable"]["session"]
    rows, chunks = await _detect_stale(center, session)
    _emit_citations(chunks)
    _emit_step("detect_stale_docs", {"center": center}, len(rows))
    return fmt.format_stale_candidates(rows, center)


@tool
async def submit_proposal(summary: str, relation_ids: list[int], reason: str,
                          config: RunnableConfig) -> str:
    """提交「标记文档锚点过时」的结构化提案（**完成全部排查后再调用，通常只调一次**），提交后进入人工确认环节，
    批准后才会真正写库标记过时。

    - summary：提案正文（中文，写清每个锚点为何过时 + 建议如何更新文档；会展示在审批框给用户看）。
    - relation_ids：待标记过时的 chunk_relations.relation_id 列表（**必须来自 detect_stale_docs 的返回，不要编造**）。
    - reason：标记过时的统一理由（写入 stale_reason）。
    """

    session: AsyncSession = config["configurable"]["session"]
    anchors = await _validate_anchors(relation_ids, session)
    if not anchors:
        _emit_step("submit_proposal", {"n": 0}, 0)
        return ("（提交的 relation_id 无效：不存在/已标记过时/非文档-代码关系。"
                "请用 detect_stale_docs 重新获取有效的 relation_id 后再提交。）")
    _emit_step("submit_proposal", {"n": len(anchors)}, len(anchors))
    if (w := _safe_writer()) is not None:
        try:
            w({"event": _PROPOSAL_EVENT,
               "data": {"summary": summary, "anchors": anchors, "reason": reason}})
        except Exception:  # noqa: BLE001
            pass
    labels = ", ".join(a.get("anchor_key") or f"#{a['relation_id']}" for a in anchors)
    return f"已提交提案：将 {len(anchors)} 个锚点（{labels}）标记为过时。等待人工确认后才会应用。"
