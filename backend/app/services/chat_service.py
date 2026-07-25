"""问答编排：检索 → 组装上下文 → LLM 流式生成 → SSE 事件，并持久化会话/消息/检索详情。

落库策略（单用户，请求级 AsyncSession）：
  - 首条消息自动建会话（标题取首问）；后续消息按 conversation_id 归属。
  - 每轮写 user 消息 + assistant 消息；assistant 消息关联一条 retrieval_logs
    （含召回漏斗 meta 与精排候选），供检索详情 / 反馈 / 后续 LTR 复用。
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.llm_client import llm
from app.core.ids import prefixed_id
from app.db.models.chat import ChatMessage, Conversation
from app.db.models.system import RetrievalLog
from app.retrieval.pipeline import pipeline

SYSTEM_PROMPT = (
    "你是 CodeRAG 代码知识库助手。请严格依据下方【引用片段】回答用户问题，"
    "在回答中用 [代码]/[文档] 标注来源；若引用片段不足以回答，请明确说明。"
    "回答简洁、聚焦，代码用代码块呈现。"
)

_TITLE_MAX = 40


def _label(r: dict) -> str:
    if r["kind"] == "code":
        return f"[代码] {r.get('class_name')}.{r.get('method_name')}"
    return "[文档] " + " > ".join(r.get("heading_path") or [])


def _derive_title(query: str) -> str:
    q = query.strip().replace("\n", " ")
    return q[:_TITLE_MAX] + ("…" if len(q) > _TITLE_MAX else "")


def build_context(ranked: list[dict], *, max_chars: int = 2000) -> str:
    parts: list[str] = []
    for i, r in enumerate(ranked, 1):
        parts.append(f"{i}. {_label(r)}\n{r['content'][:max_chars]}")
    return "\n\n".join(parts)


def build_messages(query: str, context: str, agent_type: str | None = None) -> list[dict]:
    role = f"\n\n（当前 Agent：{agent_type}）" if agent_type else ""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{query}\n\n=== 引用片段 ===\n{context or '（无）'}{role}"},
    ]


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


def _citation(r: dict) -> dict:
    return {
        "type": r["kind"], "chunk_id": r["chunk_id"], "label": _label(r),
        "class": r.get("class_name"), "method": r.get("method_name"),
        "path": r.get("heading_path"), "score": round(float(r["score"]), 3),
    }


async def stream_chat(
    session: AsyncSession, query: str, *, top_k: int = 8, agent_type: str | None = None,
    conversation_id: str | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """产出 SSE 事件并落库。事件：conversation / retrieval / citation / token / done。"""
    # ---- 1. 解析或新建会话 ----
    conv: Conversation | None = None
    if conversation_id:
        conv = await session.get(Conversation, conversation_id)
    if conv is None:
        conversation_id = prefixed_id("conv")
        conv = Conversation(
            conversation_id=conversation_id, title=_derive_title(query),
            agent_type=agent_type, message_count=0,
        )
        session.add(conv)
        await session.flush()
    yield ("conversation", {"conversation_id": conversation_id, "title": conv.title,
                            "agent_type": conv.agent_type})

    # ---- 2. user 消息 ----
    session.add(ChatMessage(
        message_id=prefixed_id("msg"), conversation_id=conversation_id,
        role="user", content=query, agent_type=agent_type,
    ))
    conv.message_count = (conv.message_count or 0) + 1
    await session.commit()

    # ---- 3. 检索（召回 → RRF → 精排）----
    ranked, meta = await pipeline.recall(session, query, top_k=top_k)
    yield ("retrieval", meta)
    citations = [_citation(r) for r in ranked]
    for cit in citations:
        yield ("citation", cit)

    # ---- 4. 检索日志（漏斗 meta + 精排候选，供检索详情/反馈/LTR）----
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
    )
    session.add(rlog)
    await session.flush()

    # ---- 5. LLM 流式生成（累积落库）----
    context = build_context(ranked)
    parts: list[str] = []
    if llm.configured:
        messages = build_messages(query, context, agent_type)
        try:
            async for tok in llm.stream_tokens(messages):
                parts.append(tok)
                yield ("token", {"content": tok})
        except Exception as e:  # noqa: BLE001
            msg = f"\n[LLM 调用失败：{type(e).__name__}: {e}]"
            parts.append(msg)
            yield ("token", {"content": msg})
        if not parts:
            fallback = "（模型未返回内容）"
            parts.append(fallback)
            yield ("token", {"content": fallback})
    else:
        notice = _no_key_notice(meta)
        parts.append(notice)
        yield ("token", {"content": notice})

    # ---- 6. assistant 消息 ----
    assistant_id = prefixed_id("msg")
    session.add(ChatMessage(
        message_id=assistant_id, conversation_id=conversation_id,
        role="assistant", content="".join(parts),
        citations=citations or None, retrieval_log_id=rlog.log_id, agent_type=agent_type,
    ))
    conv.message_count = (conv.message_count or 0) + 1
    await session.commit()

    yield ("done", {"citations": len(citations), "message_id": assistant_id,
                    "conversation_id": conversation_id})
