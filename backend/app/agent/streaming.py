"""LangGraph → SSE 适配器：把主图的 custom 流事件转成与 legacy stream_chat 同构的 (event, data)。

职责拆分：
  - 节点（retrieve/generate）通过 get_stream_writer 推 retrieval / citation / token 事件；
  - 本适配器负责会话/消息/RetrievalLog 落库（复用 chat_service 的持久化 helper，保证两路同构），
    并在图跑完后补发 conversation（前置）/ done（后置）事件。

会话/消息/检索日志的写库顺序与 legacy stream_chat 完全一致。
M41：入口建 SpanCollector + request span + configurable 注入；持久化改 dict payload（version 2）。
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.citation_enforcer import enforce
from app.agent.cost import make_cost_controller
from app.agent.graph import get_graph
from app.agent.trace import SpanCollector
from app.core.config import settings
from app.db.models.chat import Conversation
from app.domain_packs.models import DomainPack
from app.domain_packs.registry import build_whitelist, get_registry
from app.services.chat_service import (
    add_assistant_message,
    add_user_message,
    finalize_interrupted_message,
    load_conversation_history,
    open_conversation,
    persist_retrieval_log,
)


def resolve_active_pack(conv: Conversation) -> DomainPack | None:
    """请求期解析激活的领域包：conv.target_repo → settings.domain_pack_default_repo
    (or repo_path) → registry.active_for_repo。无包/无匹配 → None。"""
    repo = conv.target_repo or settings.domain_pack_default_repo or settings.repo_path
    return get_registry().active_for_repo(repo)


def _enforce_into_stream(answer: str, citations: list[dict], retrieval_meta: dict,
                         *, whitelist: Callable[[str], bool] | None = None,
                         ) -> tuple[str, list[dict]]:
    """opt-in 跑 CitationEnforcer；返回 (含 notice 的 answer, 额外 token 事件 data 列表)；
    把指标塞 ``retrieval_meta['enforcement']``。读 settings；关 = 空操作（仅写 enabled:false）。"""
    if not settings.citation_enforce_enabled:
        retrieval_meta["enforcement"] = {"enabled": False}
        return answer, []
    try:
        res = enforce(
            answer, citations,
            whitelist=whitelist,
            min_unverified=settings.citation_enforce_min_unverified,
            max_listed=settings.citation_enforce_max_listed,
        )
    except Exception:  # noqa: BLE001  纯函数兜底：永不中断请求
        retrieval_meta["enforcement"] = {"enabled": True, "error": True}
        return answer, []
    retrieval_meta["enforcement"] = {
        "enabled": True,
        "verified_count": res["verified_count"],
        "unverified_ids": res["unverified_ids"],
        "ratio": res["ratio"],
    }
    notice = res["notice"]
    if notice:
        return answer + notice, [{"content": notice}]
    return answer, []


async def stream_graph(
    session: AsyncSession, query: str, *, top_k: int = 8, agent_type: str | None = None,
    conversation_id: str | None = None, target_repo: str | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """产出 SSE 事件并落库。事件序：conversation → retrieval → citation(s) → token(s) → done。"""
    # ---- 1. 会话 + user 消息（同 legacy）----
    conv, conversation_id = await open_conversation(session, query, agent_type, conversation_id, target_repo=target_repo)
    yield ("conversation", {"conversation_id": conversation_id, "title": conv.title,
                            "agent_type": conv.agent_type})
    current_msg_id = await add_user_message(session, conv, query, agent_type)

    # ---- 2. 跑主图，转发节点推的 custom 事件，同时累积落库所需数据 ----
    # 跨轮记忆：从 chat_messages 载入先前轮次注入 state.history，供 generate 节点与场景 Agent 携带。
    history = await load_conversation_history(
        session, conversation_id, exclude_message_id=current_msg_id,
        limit=settings.conversation_history_turns,
    )
    # M37：请求期 resolve 激活包，name 注入 state（图节点经 registry.get(name) 取 pack 对象）。
    active_pack = resolve_active_pack(conv)
    repo_key = conv.target_repo or settings.domain_pack_default_repo or settings.repo_path
    state = {"query": query, "conversation_id": conversation_id,
             "agent_type": agent_type, "history": history,
             "active_pack_name": active_pack.manifest.name if active_pack else None,
             "repo_key": repo_key}
    # M41：请求级 SpanCollector + request span
    collector = SpanCollector()
    # M42：请求级预算控制器（开关 off → None，零开销）
    cost = make_cost_controller()
    config = {"configurable": {
        "thread_id": conversation_id,
        "session": session,
        "top_k": top_k,
        "agent_type": agent_type,
        "trace": collector,
        "cost": cost,
    }}
    retrieval_meta: dict = {}
    citations: list[dict] = []
    answer_parts: list[str] = []

    graph_app = get_graph()
    with collector.span("request", "chat"):
        async for chunk in graph_app.astream(state, config=config, stream_mode="custom"):
            event = chunk.get("event")
            data = chunk.get("data", {})
            yield (event, data)
            if event == "retrieval":
                retrieval_meta = data
            elif event == "citation":
                citations.append(data)
            elif event == "token":
                answer_parts.append(data.get("content", ""))

    # ---- 2.5 HITL 中断检测（M10）：图因节点 interrupt() 暂停 → custom 流到此结束 ----
    snap = await graph_app.aget_state(config)
    interrupts = _extract_interrupts(snap)
    if interrupts:
        # 落「中断」assistant 消息（retrieval_logs 已含 propose 的漏斗，抽屉可见）；发 interrupt 事件，不发 done
        proposal = interrupts[0].value
        if cost is not None:
            retrieval_meta["cost"] = cost.to_meta()   # M42：预算账本随 meta 落 JSONB
        rlog = await persist_retrieval_log(
            session, query, retrieval_meta, citations,
            agent_steps=collector.to_payload(),   # M41：dict 新形状
        )
        assistant_id = await add_assistant_message(
            session, conv, "（等待人工确认）", citations, rlog.log_id, agent_type, status="interrupted",
        )
        yield ("interrupt", {"proposal": proposal, "message_id": assistant_id,
                             "conversation_id": conversation_id})
        return

    # ---- 3. 检索日志 + assistant 消息 + done（同 legacy）----
    answer = "".join(answer_parts)
    # M36/M37：active_pack 已在 state 构建前 resolve（M37 注入 active_pack_name 复用之）。
    collab_whitelist = build_whitelist(active_pack)
    # M34：opt-in 幻觉校验——跑完图后、持久化前；notice 作 token 事件流出 + 并入 answer。
    answer, _enforce_tokens = _enforce_into_stream(answer, citations, retrieval_meta,
                                                   whitelist=collab_whitelist)
    for _td in _enforce_tokens:
        yield ("token", _td)
    if cost is not None:
        retrieval_meta["cost"] = cost.to_meta()   # M42：预算账本随 meta 落 JSONB
    rlog = await persist_retrieval_log(
        session, query, retrieval_meta, citations,
        agent_steps=collector.to_payload(),   # M41：dict 新形状
    )
    assistant_id = await add_assistant_message(
        session, conv, answer, citations, rlog.log_id, agent_type,
    )
    yield ("done", {"citations": len(citations), "message_id": assistant_id,
                    "conversation_id": conversation_id})


async def resume_graph(
    session: AsyncSession, *, conversation_id: str, message_id: str, decision: dict,
) -> AsyncIterator[tuple[str, dict]]:
    """HITL 续跑：在同一 thread（``conversation_id``）上用 ``Command(resume=decision)`` 续跑主图，
    转发 apply/reject 的 token 事件，跑完把中断消息落库为完成态并发 done。

    图状态来自 checkpoint（M8：postgres 跨重启存活 / memory 进程内）；``session`` 为本请求级，
    经 ``configurable`` 注入续跑节点（不进被 checkpoint 的 state）。
    """
    config = {"configurable": {"thread_id": conversation_id, "session": session,
                               "message_id": message_id}}
    answer_parts: list[str] = []
    async for chunk in get_graph().astream(
        Command(resume=decision), config=config, stream_mode="custom",
    ):
        event = chunk.get("event")
        data = chunk.get("data", {})
        yield (event, data)
        if event == "token":
            answer_parts.append(data.get("content", ""))
    await finalize_interrupted_message(session, message_id, "".join(answer_parts))
    yield ("done", {"message_id": message_id, "conversation_id": conversation_id})


def _extract_interrupts(snap) -> list:
    """从 StateSnapshot 提取待审批 interrupt（兼容 ``snap.tasks[*].interrupts`` 与 ``snap.interrupts``）。"""
    return ([i for t in snap.tasks for i in (t.interrupts or ())]
            or list(getattr(snap, "interrupts", None) or []))


async def continue_graph(
    session: AsyncSession, *, conversation_id: str, message_id: str | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """通用续跑（M14 Part C）：在已存在 thread（``conversation_id``）上推进执行。

    与 :func:`resume_graph`（``Command(resume=decision)`` 给 HITL 中断注入人工决策）互补——
    这里用 ``astream(None)``：不注入新输入，从 checkpoint 续跑。语义：

      - **有待审批 interrupt** → ``astream(None)`` 清不了 ``interrupt()``（会原地再暂停），
        故如实发 ``interrupt`` 事件（引导前端走 ``/resume``），不假装推进。
      - **无 interrupt 但有 pending 节点**（如 SSE 断流致 generate 被取消、checkpoint 停在上一完成的
        超步）→ ``astream(None)`` 重跑到收尾，桥接 token，建 ``completed`` 消息，发 ``done``。
      - **无 pending 节点**（线程已在 END，或无 checkpoint——如已被超时过期清理）→ 发 noop ``done``
        （``message_id=None``），不建消息、不调 ``astream(None)``（对无 checkpoint 的线程调会抛
        ``EmptyInputError``）。

    最佳努力恢复，不持久化 retrieval_log（``retrieval_log_id`` 留空）。
    """
    config = {"configurable": {"thread_id": conversation_id, "session": session}}
    graph_app = get_graph()
    snap = await graph_app.aget_state(config)
    interrupts = _extract_interrupts(snap)
    if interrupts:
        proposal = interrupts[0].value
        assistant_id = message_id
        if assistant_id is None:  # 调用方未指定 → 建一条中断态消息承载审批入口
            conv = await session.get(Conversation, conversation_id)
            assistant_id = await add_assistant_message(
                session, conv, "（等待人工确认）", [], None, None, status="interrupted",
            )
        yield ("interrupt", {"proposal": proposal, "message_id": assistant_id,
                             "conversation_id": conversation_id})
        return

    # 无 pending 节点 → 无可推进（线程已 END 或无 checkpoint）：noop done，避免对无 checkpoint
    # 线程调 astream(None) 触发 EmptyInputError。
    if not getattr(snap, "next", None):
        yield ("done", {"message_id": None, "conversation_id": conversation_id,
                        "note": "线程无可推进的工作（已完成或不存在）"})
        return

    answer_parts: list[str] = []
    citations: list[dict] = []
    async for chunk in graph_app.astream(None, config=config, stream_mode="custom"):
        event = chunk.get("event")
        data = chunk.get("data", {})
        yield (event, data)
        if event == "citation":
            citations.append(data)
        elif event == "token":
            answer_parts.append(data.get("content", ""))
    if not answer_parts:
        yield ("done", {"message_id": None, "conversation_id": conversation_id,
                        "note": "续跑未产出内容"})
        return
    conv = await session.get(Conversation, conversation_id)
    assistant_id = await add_assistant_message(
        session, conv, "".join(answer_parts), citations, None, None,
    )
    yield ("done", {"message_id": assistant_id, "conversation_id": conversation_id})
