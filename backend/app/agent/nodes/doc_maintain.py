"""文档维护 HITL 节点（Phase 7 Milestone 10 人在回路中断 + Milestone 13 完整 ReAct）。

主图 4 节点链，由显式 ``agent_type=DOC_MAINTAIN`` 触发（router 经 AgentRegistry
路由到 ``propose``）。**interrupt() 放在主图节点**（非嵌套 create_react_agent 内）——
resume 才可靠（详见 milestone 设计：子图内 interrupt 的 resume 会重启而非续跑）。

拓扑：
  propose  →  confirm  →  apply | reject  →  post_process → END
    │ReAct 排查（M13）        interrupt(proposal)   批准→写 is_stale / 拒绝→不改
    │无 LLM→硬编码降级（M10）                        （apply 循环标记多个锚点）
    │无过时锚点 → 直接到 post_process（发一条说明 token）

- ``propose``（M13 ReAct）：LLM 自主用工具排查「哪些文档锚点与代码脱节」——定位代码 →
  ``detect_stale_docs`` 找锚点+staleness 证据 → ``read_code``/``read_doc`` 精读两侧 →
  ``get_recent_changes`` 看代码近期改动 → ``submit_proposal`` 提交**结构化、可多锚点**提案。
  ``submit_proposal`` 经内部协议事件 ``_PROPOSAL_EVENT`` 把提案回传本节点（捕获、**不桥接到 SSE**），
  写入 ``state.proposal``/``stale_anchors``。agent_step/citation **桥接**到主图（抽屉可见——M10 没有的
  可观测性）。**不流 token**（避免中断前漏半句）。无 LLM key 或 ReAct 异常 → 降级到 M10 硬编码排查
  （``_propose_fallback``：recall→选单锚点→模板起草），HITL 仍可演示。
- ``confirm``：``decision = interrupt(proposal)``——首轮暂停（状态入 checkpoint）；resume 时
  本节点从头重跑，``interrupt()`` 返回 ``Command(resume=...)`` 传入的决策 dict。
- ``apply``：批准 → ① 循环 ``_apply_stale_mark`` 写 ``chunk_relations.is_stale=True``（多锚点）；
  ② 按 doc_chunk_id 去重，据代码 LLM 重写过时文档段落、工件写回 MinIO（``generate_doc_update``）；
  ③ 装配 PR 提案落 ``doc_update_proposals`` 表（``create_doc_pr``，仅载荷、不执行 git）。全程发 token 摘要、
  永不中断请求（无 LLM → 记 PENDING_MANUAL）。M15 闭合写动作弧线。
- ``reject``：拒绝 → 发 ❌ token，不改库。

被闸门的写动作 = 标记锚点过时（真实、可逆、复用既有列、无新表）。opt-in（写动作 Agent 不接 intent）。
"""
from __future__ import annotations

import warnings
from contextlib import nullcontext

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agents._base import _emit_retrieval_meta
from app.agent.llm import TraceCallbackHandler, configured, get_chat_model
from app.agent.state import AgentState
from app.agent.tools.code_tools import get_recent_changes, read_code, search_code, search_symbol
from app.agent.tools.doc_tools import read_doc
from app.agent.tools.maintain_tools import _PROPOSAL_EVENT, detect_stale_docs, submit_proposal
from app.clients.llm_client import llm
from app.core.config import settings
from app.db.models.relation import ChunkRelation
from app.retrieval.pipeline import pipeline
from app.services.chat_service import _citation, _enrich_content_types
from app.services.doc_maintenance_service import create_doc_pr, generate_doc_update


def _safe_writer():
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001
        return None


def _emit_token(content: str) -> None:
    if (w := _safe_writer()) is None:
        return
    try:
        w({"event": "token", "data": {"content": content}})
    except Exception:  # noqa: BLE001
        pass


def _anchor_label(anchor: dict) -> str:
    """锚点可读标签：anchor_key（可能为 NULL）→ source_chunk_id → relation#id 兜底。"""
    return (anchor.get("anchor_key")
            or anchor.get("source_chunk_id")
            or f"relation#{anchor.get('relation_id')}")


async def _pick_stale_candidate(
    session: AsyncSession, ranked: list[dict],
) -> dict | None:
    """从召回的 chunk 中选一条**未过时**的文档↔代码锚点关系（confidence 最高者）。

    无 LLM key 时的降级排查路径（M10）用此函数。命中样本库的 Foo↔doc DOC_TO_CODE/CODE_TO_DOC 关系；
    无候选返回 None（上层优雅降级）。返回 ``{relation_id, anchor_key, source_chunk_id, target_chunk_id, relation_type}``。
    """
    ids = [r["chunk_id"] for r in ranked]
    if not ids:
        return None
    row = (await session.execute(
        select(
            ChunkRelation.relation_id, ChunkRelation.anchor_key,
            ChunkRelation.source_chunk_id, ChunkRelation.target_chunk_id,
            ChunkRelation.relation_type,
        ).where(
            ChunkRelation.relation_type.in_(["DOC_TO_CODE", "CODE_TO_DOC"]),
            ChunkRelation.is_stale.is_(False),
            (ChunkRelation.source_chunk_id.in_(ids)
             | ChunkRelation.target_chunk_id.in_(ids)),
        ).order_by(ChunkRelation.confidence.desc()).limit(1)
    )).first()
    if row is None:
        return None
    return {"relation_id": row.relation_id, "anchor_key": row.anchor_key,
            "source_chunk_id": row.source_chunk_id, "target_chunk_id": row.target_chunk_id,
            "relation_type": row.relation_type}


async def _apply_stale_mark(session: AsyncSession, anchor: dict, reason: str) -> int:
    """闸门内写动作：把锚点关系标记为过时（is_stale=True + stale_reason）。返回受影响行数。"""
    result = await session.execute(
        update(ChunkRelation)
        .where(ChunkRelation.relation_id == anchor["relation_id"])
        .values(is_stale=True, stale_reason=reason[:256])
    )
    await session.commit()
    return result.rowcount or 1


def _split_anchor(anchor: dict) -> tuple[str | None, str | None]:
    """按 relation_type 派生 ``(doc_chunk_id, code_chunk_id)``。
    约定：``DOC_TO_CODE`` ⇒ source=doc/target=code；``CODE_TO_DOC`` ⇒ source=code/target=doc。
    用 ``.get`` 防御（生产锚点必有这两列；缺失返回 None，交由上层 fetch 失败降级）。"""
    src, tgt = anchor.get("source_chunk_id"), anchor.get("target_chunk_id")
    if anchor.get("relation_type") == "CODE_TO_DOC":
        return tgt, src
    return src, tgt


def _format_proposal(upd: dict, pr: dict) -> str:
    """把一次「重写 + PR 提案」结果格式化成 token 摘要行。"""
    head = " › ".join(upd.get("heading_path") or []) or upd.get("file_path") or "文档段落"
    pid = pr.get("proposal_id")
    pid_s = f"#{pid}" if pid is not None else "（未落库）"
    if upd.get("rewritten_ok"):
        artifact = upd.get("artifact_key") or "（工件写入失败）"
        return (f"📝 文档更新提案 {pid_s}：已重写「{head}」并生成 PR 载荷"
                f"（分支 {pr.get('branch_name')} / 状态 {pr.get('status')} / 工件 {artifact}）。")
    note = {"no_llm": "未配置 LLM，已记录待人工重写", "chunk_not_found": "未取到文档/代码段落",
            "llm_error": "LLM 重写失败", "llm_empty": "LLM 返回为空"}.get(upd.get("reason"), "未能重写")
    return f"📝 文档更新提案 {pid_s}：「{head}」{note}（状态 {pr.get('status')}）。"


async def _draft_proposal(query: str, anchor: dict) -> str:
    """非流式起草「标记锚点过时」提案；未配置 LLM 或失败→模板兜底（绝不阻塞流程）。

    无 LLM key 时的降级排查路径（M10）用此函数。
    """
    anchor_key = _anchor_label(anchor)
    fallback = (
        f"建议将文档-代码锚点「{anchor_key}」标记为过时。\n"
        f"依据：用户提问「{query.strip()[:60]}」及相关检索结果，该锚点关联疑似失效，需人工确认。"
    )
    if not llm.configured:
        return fallback
    messages = [
        {"role": "system", "content": "你是 CodeRAG 文档维护助手。用一两句中文起草一个『标记文档-代码锚点过时』的提案，明确锚点与简要理由。"},
        {"role": "user", "content": f"用户问题：{query}\n锚点：{anchor_key}\n关联类型：{anchor.get('relation_type')}"},
    ]
    try:
        text = await llm.chat(messages, temperature=0.2, max_tokens=200)
        return (text.strip() or fallback)
    except Exception:  # noqa: BLE001
        return fallback


async def _propose_fallback(state: AgentState, config: RunnableConfig) -> dict:
    """无 LLM key 或 ReAct 异常时的降级排查（M10 硬编码路径）。

    recall → 选单条 confidence 最高锚点 → 模板/非流式起草。发真实 retrieval/citation；返回
    ``stale_anchors=[anchor]``（有锚点，不发 token）或 ``stale_anchors=[]``（无锚点，发说明 token）。
    """
    session: AsyncSession = config["configurable"]["session"]
    top_k = config["configurable"].get("top_k", 8)
    ranked, meta = await pipeline.recall(session, state["query"], top_k=top_k)
    await _enrich_content_types(session, ranked)

    writer = _safe_writer()
    if writer:
        writer({"event": "retrieval", "data": meta})
        for r in ranked:
            writer({"event": "citation", "data": _citation(r)})

    citations = [_citation(r) for r in ranked]
    anchor = await _pick_stale_candidate(session, ranked)
    if anchor is None:
        _emit_token("（未找到可标记的文档-代码锚点关系，无需操作。）")
        return {"ranked": ranked, "retrieval_meta": meta, "citations": citations,
                "proposal": None, "stale_anchors": []}

    proposal = await _draft_proposal(state["query"], anchor)
    return {"ranked": ranked, "retrieval_meta": meta, "citations": citations,
            "proposal": proposal, "stale_anchors": [anchor]}


# ---- ReAct Agent 工厂（M13）----

MAINTAIN_PROMPT = (
    "你是 CodeRAG 的【文档维护 Agent】，目标是找出与代码脱节的过时文档锚点，并提案把它们标记为过时。\n"
    "工作方式（ReAct）：先定位用户关心的代码（search_symbol 按名 / search_code 按描述）→ "
    "detect_stale_docs 找出该代码的文档↔代码锚点（每条带 relation_id 与代码最近变更证据）→ "
    "read_code / read_doc 精读锚点两侧（代码实现 vs 文档描述）比对是否一致 → "
    "get_recent_changes 看代码近期是否被改动（代码改了而文档没跟就是过时信号）→ "
    "判定确实脱节的，用 submit_proposal 提交结构化提案（可一次标记多个锚点）。\n"
    "可用工具：search_symbol、search_code、read_code、read_doc、get_recent_changes、"
    "detect_stale_docs、submit_proposal。\n"
    "规则：① 判定必须基于真实读到的代码/文档，不要臆造；② 只提案确实脱节的锚点"
    "（代码已改而文档未更 / 两侧描述矛盾），宁缺毋滥——证据不足就不要提案；③ submit_proposal 的 "
    "relation_ids **必须来自 detect_stale_docs 的返回**，不要编造 id；④ submit_proposal 的 summary "
    "用中文写清每个锚点为何过时 + 建议如何更新文档（这段会展示给用户审批）；⑤ 用中文、控制在 6 步内，"
    "不重复读取同一 chunk；⑥ 排查后若未发现过时锚点，就不要调用 submit_proposal，直接作答说明即可。"
)

#: 文档维护 Agent 绑定的工具集（定位 + 两侧精读 + 变更证据 + 检测 + 提交）
MAINTAIN_TOOLS = [
    search_symbol, search_code, read_code, read_doc, get_recent_changes,
    detect_stale_docs, submit_proposal,
]

_agent = None


def get_doc_maintain_agent():
    """惰性单例：create_react_agent（默认 state_schema，绑定文档维护工具集）。"""
    global _agent
    if _agent is None:
        with warnings.catch_warnings():
            # langgraph-prebuilt 的 create_react_agent 在 v1 标记弃用（迁往 langchain.agents），
            # 但 langchain 包未安装，功能在 langgraph 内仍完整。抑制该告警保持日志干净。
            warnings.simplefilter("ignore")
            from langgraph.prebuilt import create_react_agent
            _agent = create_react_agent(get_chat_model(), MAINTAIN_TOOLS, prompt=MAINTAIN_PROMPT)
    return _agent


# ---- 主图节点 ----


async def propose(state: AgentState, config: RunnableConfig) -> dict:
    """ReAct 排查并起草结构化提案（不发 token，避免中断前漏半句）。

    - 无 LLM key → ``_propose_fallback``（M10 硬编码路径，HITL 仍可演示）；
    - 有 LLM key → 跑 ``get_doc_maintain_agent``，桥接 agent_step/citation 到主图（抽屉可见），
      捕获 ``submit_proposal`` 推的 ``_PROPOSAL_EVENT``（不桥接），落 ``proposal``/``stale_anchors``；
    - ReAct 异常 → 降级 token + 空 anchors → post_process；
    - Agent 结论无过时 → 说明 token + 空 anchors → post_process。
    M41：collector 存在时注入 TraceCallbackHandler + 包 agent span；无 collector → 零开销。
    """
    if not configured():
        return await _propose_fallback(state, config)

    collector = config["configurable"].get("trace")
    _emit_retrieval_meta(state, agent_name="DOC_MAINTAIN", tools=MAINTAIN_TOOLS)
    cfg = dict(config)
    cfg["configurable"] = dict(config.get("configurable") or {})
    # M41：collector 存在时注入 TraceCallbackHandler（记 llm span）；无 collector → 不注入（零开销）
    if collector is not None:
        existing = cfg.get("callbacks")
        cb = TraceCallbackHandler(collector)
        if existing is None:
            cfg["callbacks"] = [cb]
        elif isinstance(existing, list):
            cfg["callbacks"] = [*existing, cb]
        else:
            cfg["callbacks"] = [*(getattr(existing, "handlers", []) or []), cb]
    cfg["recursion_limit"] = settings.agent_max_iterations * 2 + 3
    parent_writer = _safe_writer()  # 主图 custom 流；Agent 嵌套 custom 事件需手动桥接上来
    holder: dict = {}

    try:
        agent = get_doc_maintain_agent()
        seed = [*state.get("history", []), {"role": "user", "content": state["query"]}]
        cm = (collector.span("agent", "doc_maintain")
              if collector is not None else nullcontext())
        with cm:
            async for chunk in agent.astream({"messages": seed}, config=cfg, stream_mode="custom"):
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("event") == _PROPOSAL_EVENT:
                    holder.update(chunk.get("data") or {})  # 捕获结构化提案
                    continue  # 内部协议事件，不桥接到主图 SSE
                if parent_writer:
                    parent_writer(chunk)  # agent_step / citation 桥接到主图
    except Exception:  # noqa: BLE001
        _emit_token("（文档维护 Agent 异常，未能完成排查。）")
        return {"proposal": None, "stale_anchors": []}

    anchors = holder.get("anchors") or []
    if anchors:
        return {"proposal": holder.get("summary"), "stale_anchors": anchors,
                "stale_reason": holder.get("reason")}
    _emit_token("（经排查未发现需要标记的过时文档锚点。）")
    return {"proposal": None, "stale_anchors": []}


async def confirm(state: AgentState) -> dict:
    """HITL 闸门：``interrupt(proposal)`` 首轮暂停（状态入 checkpoint）；resume 重跑本节点时
    返回人工决策 dict（``{approved, comment}``）。节点须极简——resume 会从头重跑。"""
    decision = interrupt(state.get("proposal") or "")
    return {"decision": decision}


async def apply_stale(state: AgentState, config: RunnableConfig) -> dict:
    """批准分支：① 循环标记多个锚点过时（is_stale）；② 据代码 LLM 重写过时文档段落、工件写回 MinIO；
    ③ 装配 PR 提案落 ``doc_update_proposals`` 表（仅载荷、不执行 git）。全程发 token 摘要、永不中断请求。

    多锚点指向同一文档段落时按 doc_chunk_id 去重（只重写一次、relation_ids 聚合）。
    无 LLM / 重写失败 → 提案记 ``PENDING_MANUAL``，is_stale 标记仍完成（既有行为不退步）。
    """
    session: AsyncSession = config["configurable"]["session"]
    anchors = state.get("stale_anchors") or []
    decision = state.get("decision") or {}
    reason = decision.get("comment") or state.get("stale_reason") or "HITL 人工确认标记过时"
    conversation_id = state.get("conversation_id")

    # ① 标记过时（既有行为）
    marked = [_anchor_label(a) for a in anchors]
    for anchor in anchors:
        await _apply_stale_mark(session, anchor, reason)
    mark_text = (f"✅ 已将 {len(marked)} 个锚点标记为过时：" + "、".join(marked)
                 if marked else "✅ 已完成（无锚点可标记）。")
    _emit_token(mark_text)
    parts = [mark_text]

    # ② + ③ 按 doc_chunk_id 去重 → 重写 + PR 提案
    seen: dict[str, dict] = {}
    for anchor in anchors:
        doc_cid, code_cid = _split_anchor(anchor)
        slot = seen.setdefault(doc_cid, {"doc_chunk_id": doc_cid, "code_chunk_id": code_cid,
                                         "relation_ids": []})
        slot["relation_ids"].append(anchor["relation_id"])
    for slot in seen.values():
        upd = await generate_doc_update(
            session, doc_chunk_id=slot["doc_chunk_id"], code_chunk_id=slot["code_chunk_id"],
        )
        pr = await create_doc_pr(
            session, conversation_id=conversation_id, file_id=upd["file_id"],
            doc_chunk_id=slot["doc_chunk_id"], heading_path=upd["heading_path"],
            relation_ids=slot["relation_ids"], original_text=upd["original_text"],
            rewritten_text=upd["rewritten_text"], artifact_key=upd["artifact_key"],
        )
        line = _format_proposal(upd, pr)
        _emit_token(line)
        parts.append(line)

    return {"answer": "\n\n".join(parts)}


async def reject(state: AgentState) -> dict:
    """拒绝分支：不改库，发 ❌ token。"""
    text = "❌ 已取消，未做任何修改。"
    _emit_token(text)
    return {"answer": text}


# ---- 条件路由 ----


def after_propose(state: AgentState) -> str:
    """有过时锚点→confirm（闸门）；无→post_process（已发过说明 token）。"""
    return "confirm" if state.get("stale_anchors") else "post_process"


def after_confirm(state: AgentState) -> str:
    """人工批准→apply；否则 reject。"""
    return "apply" if (state.get("decision") or {}).get("approved") else "reject"
