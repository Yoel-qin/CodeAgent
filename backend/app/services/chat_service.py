"""问答编排：检索 → 组装上下文 → LLM 流式生成 → SSE 事件，并持久化会话/消息/检索详情。

落库策略（单用户，请求级 AsyncSession）：
  - 首条消息自动建会话（标题取首问）；后续消息按 conversation_id 归属。
  - 每轮写 user 消息 + assistant 消息；assistant 消息关联一条 retrieval_logs
    （含召回漏斗 meta 与精排候选），供检索详情 / 反馈 / 后续 LTR 复用。
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.cost import BudgetExceeded, make_cost_controller
from app.agent.trace import SpanCollector, llm_span
from app.clients.llm_client import llm
from app.core.config import settings
from app.core.ids import prefixed_id
from app.db.models.chat import ChatMessage, Conversation
from app.db.models.doc import DocChunk
from app.db.models.system import RetrievalLog
from app.retrieval.pipeline import pipeline

SYSTEM_PROMPT = (
    "你是 CodeRAG 代码知识库助手。请严格依据下方【引用片段】回答用户问题，"
    "在回答中用 [代码]/[文档] 标注来源；若引用片段不足以回答，请明确说明。"
    "回答简洁、聚焦，代码用代码块呈现。"
)

_TITLE_MAX = 40
# 跨轮历史每条内容截断上限（防 token 爆量）；超长加 …[已截断] 标记。
_HISTORY_MSG_MAX_CHARS = 1200


def _label(r: dict) -> str:
    if r["kind"] == "code":
        return f"[代码] {r.get('class_name')}.{r.get('method_name')}"
    ct = r.get("content_type") or "text"
    if ct == "image":
        d = (r.get("image_description") or "").strip()
        return "[图片] " + (d[:50] or "图片")
    if ct in ("table", "table_fragment"):
        path = " > ".join(r.get("heading_path") or [])
        return f"[表格] {path}" if path else "[表格]"
    return "[文档] " + " > ".join(r.get("heading_path") or [])


def _derive_title(query: str) -> str:
    q = query.strip().replace("\n", " ")
    return q[:_TITLE_MAX] + ("…" if len(q) > _TITLE_MAX else "")


def build_context(ranked: list[dict], *, max_chars: int = 2000) -> str:
    parts: list[str] = []
    for i, r in enumerate(ranked, 1):
        parts.append(f"{i}. {_label(r)}\n{r['content'][:max_chars]}")
    return "\n\n".join(parts)


def build_messages(
    query: str, context: str, agent_type: str | None = None,
    history: list[dict] | None = None,
) -> list[dict]:
    """组装 LLM 消息：system [+ 跨轮历史] 当前 user(含引用片段)。

    history 非空时插在 system 与当前 user 间（每项 ``{role, content}``）；
    None/[] 与无历史逐字一致（保证现有调用点零回归）。
    """
    role = f"\n\n（当前 Agent：{agent_type}）" if agent_type else ""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": f"{query}\n\n=== 引用片段 ===\n{context or '（无）'}{role}"})
    return messages


async def load_conversation_history(
    session: AsyncSession, conversation_id: str, *,
    exclude_message_id: str, limit: int,
) -> list[dict]:
    """载入会话历史（供跨轮记忆），返回 ``[{role, content}]`` 升序（最旧→最新）。

    - ``limit<=0`` → ``[]``（禁用，逐字同无记忆行为）。
    - 按 ``created_at DESC LIMIT limit`` 取最近 ``limit`` 条先前消息，再反转为升序时序。
    - 排除当前轮（刚由 ``add_user_message`` 写入的 ``exclude_message_id``）。
    - 每条 ``content`` 截断到 ``_HISTORY_MSG_MAX_CHARS`` + 标记，防 token 爆量。
    - 排除 ``interrupted``/``expired`` 态（HITL 占位/超时终态无有效答案，避免注入噪声——M14）。
    """
    if limit <= 0:
        return []
    rows = (await session.execute(
        select(ChatMessage.role, ChatMessage.content)
        .where(ChatMessage.conversation_id == conversation_id)
        .where(ChatMessage.message_id != exclude_message_id)
        .where(ChatMessage.status.notin_(("interrupted", "expired")))
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )).all()
    history: list[dict] = []
    for role, content in reversed(rows):  # DESC 取最近 N 条 → 反转成升序时序
        if len(content) > _HISTORY_MSG_MAX_CHARS:
            content = content[:_HISTORY_MSG_MAX_CHARS] + "…[已截断]"
        history.append({"role": role, "content": content})
    return history


def _no_key_notice(meta: dict) -> str:
    recall = meta.get("recall", {})
    rerank = f" → 粗排 {meta.get('coarse')} → 精排 {meta.get('fine')}" if meta.get("rerank_on") else ""
    return (
        "⏳ 未配置 LLM_API_KEY，已跳过生成（检索与引用正常工作）。\n"
        f"检索词：{', '.join(meta['terms'])}\n"
        f"召回：向量 {recall.get('vector', meta.get('vector', 0))} + "
        f"词法 {recall.get('lexical', meta.get('lexical', 0))} + "
        f"图遍历 {recall.get('graph', meta.get('graph', 0))}"
        f" → RRF 融合 {meta.get('merged', 0)}{rerank}。\n"
        "请在 .env 设置 DeepSeek/Qwen 的 LLM_API_KEY 以启用流式回答。"
    )


async def _enrich_content_types(session: AsyncSession, ranked: list[dict]) -> None:
    """Phase 1.5e：一次查询补 chunk_content_type（+ image_description），让 image/table 引用可区分。
    避免改每条召回路径与 ES mapping——仅在精排后补一次。"""
    ids = [r["chunk_id"] for r in ranked]
    if not ids:
        return
    rows = (await session.execute(
        select(DocChunk.chunk_id, DocChunk.chunk_content_type, DocChunk.image_description)
        .where(DocChunk.chunk_id.in_(ids))
    )).all()
    cmap = {cid: (ct or "text") for cid, ct, _ in rows}
    dmap = {cid: desc for cid, _, desc in rows}
    for r in ranked:
        r["content_type"] = cmap.get(r["chunk_id"], "text")
        if r["content_type"] == "image":
            r["image_description"] = dmap.get(r["chunk_id"])


def _citation(r: dict) -> dict:
    return {
        "type": r["kind"], "chunk_id": r["chunk_id"], "label": _label(r),
        "class": r.get("class_name"), "method": r.get("method_name"),
        "path": r.get("heading_path"), "score": round(float(r["score"]), 3),
        "content_type": r.get("content_type") or "text",
    }


# ---- M42 QA 缓存 helper（opt-in，开关 off / Redis 挂 → 零开销）----


def _qa_repo_key(conv: Conversation) -> str:
    """QA 缓存键的 repo 维度：与 pack resolve 同链，避免跨库串答案。"""
    return conv.target_repo or settings.domain_pack_default_repo or settings.repo_path


async def _qa_cache_lookup(repo: str, query: str) -> dict | None:
    """命中返回 {answer, citations, meta}；开关 off / Redis 挂 / miss -> None。"""
    from app.clients.cache_client import get_cache_client, normalize_query, qa_cache_key
    cc = get_cache_client()
    if cc is None:
        return None
    return await cc.qa_get(qa_cache_key(repo, normalize_query(query)))


async def _qa_cache_store(repo: str, query: str, *, answer: str,
                          citations: list, meta: dict) -> None:
    """生成成功后 best-effort 写入（失败仅 log，见 CacheClient 软失败）。"""
    from app.clients.cache_client import get_cache_client, normalize_query, qa_cache_key
    cc = get_cache_client()
    if cc is None:
        return
    await cc.qa_set(qa_cache_key(repo, normalize_query(query)),
                    {"answer": answer, "citations": citations, "meta": meta})


# ---- 持久化 helper（legacy stream_chat 与 LangGraph 适配器共用，保证两路同构）----


async def open_conversation(
    session: AsyncSession, query: str, agent_type: str | None,
    conversation_id: str | None, target_repo: str | None = None,
) -> tuple[Conversation, str]:
    """解析或新建会话（首条消息自动建会话，标题取首问）。返回 (conv, conversation_id)。"""
    conv: Conversation | None = None
    if conversation_id:
        conv = await session.get(Conversation, conversation_id)
    if conv is None:
        conversation_id = prefixed_id("conv")
        conv = Conversation(
            conversation_id=conversation_id, title=_derive_title(query),
            agent_type=agent_type, message_count=0, target_repo=target_repo,
        )
        session.add(conv)
        await session.flush()
    return conv, conversation_id


async def add_user_message(
    session: AsyncSession, conv: Conversation, query: str, agent_type: str | None,
) -> str:
    """写 user 消息并 commit，返回 message_id（供 load_conversation_history 排除当前轮）。"""
    message_id = prefixed_id("msg")
    session.add(ChatMessage(
        message_id=message_id, conversation_id=conv.conversation_id,
        role="user", content=query, agent_type=agent_type,
    ))
    conv.message_count = (conv.message_count or 0) + 1
    await session.commit()
    return message_id


async def persist_retrieval_log(
    session: AsyncSession, query: str, meta: dict, citations: list[dict],
    agent_steps: list[dict] | dict | None = None,
) -> RetrievalLog:
    """写检索日志（漏斗 meta + 精排候选 + Agent 工具轨迹），flush 后返回（供 assistant 消息外键）。

    M41 起新写入为 dict v2 trace 形状（旧调用方仍传扁平 list）。
    """
    rlog = RetrievalLog(
        query_text=query,
        recall_results=meta,
        fine_rank_results=[{k: v for k, v in cit.items() if k != "label"} for cit in citations],
        final_chunk_ids=[c["chunk_id"] for c in citations],
        recall_count=meta.get("merged"),
        coarse_rank_count=meta.get("coarse"),
        fine_rank_count=len(citations),
        recall_latency_ms=meta.get("recall_ms"),
        fine_rank_ms=meta.get("rerank_ms"),
        total_latency_ms=(meta.get("recall_ms") or 0) + (meta.get("rerank_ms") or 0),
        agent_steps=agent_steps or None,  # 空 list → NULL（legacy/retrieve 路径无轨迹）
    )
    session.add(rlog)
    await session.flush()
    return rlog


async def add_assistant_message(
    session: AsyncSession, conv: Conversation, content: str, citations: list[dict],
    retrieval_log_id: int, agent_type: str | None, *, status: str = "completed",
) -> str:
    """写 assistant 消息并 commit，返回 message_id。

    ``status``：``completed``（默认，既有调用方零回归）| ``interrupted``（HITL 中断态，待 resume 续写）。
    """
    assistant_id = prefixed_id("msg")
    session.add(ChatMessage(
        message_id=assistant_id, conversation_id=conv.conversation_id,
        role="assistant", content=content, status=status,
        citations=citations or None, retrieval_log_id=retrieval_log_id, agent_type=agent_type,
    ))
    conv.message_count = (conv.message_count or 0) + 1
    await session.commit()
    return assistant_id


async def finalize_interrupted_message(
    session: AsyncSession, message_id: str, content: str,
) -> None:
    """HITL resume 后：把中断态消息更新为完成态并写入最终内容（apply/reject 的产出）。"""
    msg = await session.get(ChatMessage, message_id)
    if msg is not None:
        msg.content = content
        msg.status = "completed"
        await session.commit()


async def get_message_status(session: AsyncSession, message_id: str) -> str | None:
    """返回消息当前 ``status``（无此消息 → ``None``）；供 ``/resume`` 校验是否仍处待审批态（M14）。"""
    msg = await session.get(ChatMessage, message_id)
    return msg.status if msg is not None else None


async def stream_chat(
    session: AsyncSession, query: str, *, top_k: int = 8, agent_type: str | None = None,
    conversation_id: str | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """产出 SSE 事件并落库。事件：conversation / retrieval / citation / token / done。

    按 settings.rag_engine 分流：langgraph → app/agent StateGraph（行为同构）；
    否则走下方 legacy 线性流水线（默认）。
    """
    if settings.rag_engine == "langgraph":
        # 延迟导入：避免 chat_service ↔ agent.streaming 循环，且 legacy 路径零 langgraph 开销。
        from app.agent.streaming import stream_graph
        async for event, data in stream_graph(
            session, query, top_k=top_k, agent_type=agent_type, conversation_id=conversation_id,
        ):
            yield event, data
        return

    # ---- 1. 解析或新建会话 ----
    conv, conversation_id = await open_conversation(session, query, agent_type, conversation_id)
    yield ("conversation", {"conversation_id": conversation_id, "title": conv.title,
                            "agent_type": conv.agent_type})

    # ---- 2. user 消息 ----
    current_msg_id = await add_user_message(session, conv, query, agent_type)

    # ---- M42 QA 缓存（opt-in）：同 repo+归一化 query 命中 → 跳过 recall+LLM，回放缓存答案 ----
    qa_repo = _qa_repo_key(conv)
    if (cached := await _qa_cache_lookup(qa_repo, query)) is not None:
        hit_meta = dict(cached.get("meta") or {})
        hit_meta["cache"] = "hit"
        hit_citations = list(cached.get("citations") or [])
        hit_collector = SpanCollector()
        hrq = hit_collector.start("request", "chat")
        hit_collector.end(hrq)
        yield ("retrieval", hit_meta)
        for cit in hit_citations:
            yield ("citation", cit)
        rlog_hit = await persist_retrieval_log(
            session, query, hit_meta, hit_citations,
            agent_steps=hit_collector.to_payload())
        answer_text = cached.get("answer") or ""
        for i in range(0, len(answer_text), 64):   # 切片回放，SSE 契约不变
            yield ("token", {"content": answer_text[i:i + 64]})
        assistant_id = await add_assistant_message(
            session, conv, answer_text, hit_citations, rlog_hit.log_id, agent_type,
        )
        yield ("done", {"citations": len(hit_citations), "message_id": assistant_id,
                        "conversation_id": conversation_id})
        return

    # ---- 3. 检索（召回 → RRF → 精排）----
    collector = SpanCollector()  # M41 结构化 trace
    cost = make_cost_controller()  # M42：请求级预算控制器（开关 off → None，零开销）
    rq = collector.start("request", "chat")  # 外层 span，手动管理（不入栈）
    t0 = time.perf_counter()
    ranked, meta = await pipeline.recall(session, query, top_k=top_k)
    await _enrich_content_types(session, ranked)
    collector.record("retrieval", "recall", (time.perf_counter() - t0) * 1000,
                     parent_id=rq.span_id,
                     attrs={"recall": meta.get("recall"), "merged": meta.get("merged"),
                            "rerank_on": meta.get("rerank_on"),
                            "rewritten": meta.get("rewritten", False)})
    yield ("retrieval", meta)
    citations = [_citation(r) for r in ranked]
    for cit in citations:
        yield ("citation", cit)

    # ---- 4. 检索日志（漏斗 meta + 精排候选 + 部分 trace）----
    rlog = await persist_retrieval_log(session, query, meta, citations,
                                       agent_steps=collector.to_payload())

    # ---- 5. LLM 流式生成（累积落库）----
    context = build_context(ranked)
    parts: list[str] = []
    gen_aborted = False
    if llm.configured:
        if cost is not None:
            try:
                cost.check()   # M42：生成前预算闸（legacy 单请求 ≤2 调用，防御性）
            except BudgetExceeded as e:
                parts.append(e.notice())
                yield ("token", {"content": e.notice()})
                gen_aborted = True
        if not gen_aborted:
            history = await load_conversation_history(
                session, conversation_id, exclude_message_id=current_msg_id,
                limit=settings.conversation_history_turns,
            )
            messages = build_messages(query, context, agent_type, history)
            async with llm_span(collector, llm.model, prompt_text=context,
                                 parent_id=rq.span_id) as ls:
                try:
                    async for tok in llm.stream_tokens(messages, usage_out=ls.usage_out):
                        ls.add_token(tok)
                        parts.append(tok)
                        yield ("token", {"content": tok})
                except Exception as e:  # noqa: BLE001
                    ls.mark_error(e)
                    msg = f"\n[LLM 调用失败：{type(e).__name__}: {e}]"
                    parts.append(msg)
                    yield ("token", {"content": msg})
                    gen_aborted = True
                if cost is not None:
                    # M42：usage 真值记量（拿不到 → chars/4 估算；ls 仅块内可见故在此结算）
                    u = dict(ls.usage_out)
                    if u.get("prompt_tokens") is not None:
                        cost.record_usage(prompt=u.get("prompt_tokens") or 0,
                                          completion=u.get("completion_tokens") or 0)
                    else:
                        cost.record_usage(completion=sum(len(p) for p in parts) // 4,
                                          estimated=True)
            if not parts:
                fallback = "（模型未返回内容）"
                parts.append(fallback)
                yield ("token", {"content": fallback})
    else:
        notice = _no_key_notice(meta)
        parts.append(notice)
        yield ("token", {"content": notice})

    # ---- 生成结束：同一事务补写完整 trace（request span 收口）----
    collector.end(rq)
    if cost is not None:
        meta["cost"] = cost.to_meta()   # M42：预算账本随 meta 回写（recall_results 持久化）
    try:
        rlog.agent_steps = collector.to_payload()
        if cost is not None:
            rlog.recall_results = dict(meta)   # 重赋值触发 SQLAlchemy 变更检测（JSONB 原地改不落）
        await session.flush()
    except Exception:  # noqa: BLE001  trace/cost 旁观者：补写失败不影响主流程
        pass

    # ---- M42 QA 缓存写入：生成成功（非 abort、有 key）才入缓存 ----
    if llm.configured and not gen_aborted:
        await _qa_cache_store(qa_repo, query, answer="".join(parts),
                              citations=citations, meta=meta)

    # ---- 6. assistant 消息 ----
    assistant_id = await add_assistant_message(
        session, conv, "".join(parts), citations, rlog.log_id, agent_type,
    )

    yield ("done", {"citations": len(citations), "message_id": assistant_id,
                    "conversation_id": conversation_id})
