"""streaming 适配层（Plan 3 Task 9）：主图事件流 → SSE ``(event, data)`` 对。

六步（brief 冻结，①② 为 R1 评审 F-A 修正后的顺序）：① ``open_conversation`` →
② 取 ``chat_messages`` 最近 ``settings.history_turns`` 轮（**先于**本测 user 落行，
否则同事务 flush 即读会把当前 query 泄入 history）→ user 落行 + ``conversation`` 事件
③ 组装 per-request config
（session/cost/top_k 走 ``configurable``，**不进图状态**；``recursion_limit`` 60）
④ ``GRAPH.astream(..., stream_mode="custom")`` 逐 chunk 转发 + 顺序无关累积
tokens/citations/agent_steps ⑤ 流尽聚合 answer → assistant 落行 + commit → ``done``
⑥ 整体 try/except 兜底的兜底：任何逃逸异常发 ``[内部错误: {类型名}]`` + done，
**HTTP 200 不断流**。

spec §5.1 偏差（本模块与 :mod:`app.agent.graph` 共同兑现）：spec 的独立
``post_process → END`` 节点折入本适配层——图内节点只发事件（事件即数据，节点返回
``{}``/``{"answer": None}`` 不写图状态），answer/citations/agent_steps 的聚合、
assistant 消息持久化与 done 事件全部在本层收尾（旧库 streaming adapter 同构）。

消费端契约：**不得假设 citation 先于 token**——Task 8 ReAct 路收尾 drain，事件序为
token → agent_step → citation；本层对三类事件只做类型分流累积，不依赖顺序。降级路
可能出双 ``retrieval`` 事件（mode=agent 名后被 retrieve 名覆盖）——按最后一条生效。
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.cost import CostController
from app.agent.graph import GRAPH
from app.core.config import settings
from app.services.chat_service import add_message, load_history, open_conversation

__all__ = ["stream_chat"]


async def stream_chat(
    session: AsyncSession,
    *,
    query: str,
    conversation_id: str | None = None,
    repo: str | None = None,
    top_k: int = 8,
) -> AsyncIterator[tuple[str, dict]]:
    """跑主图并把自定义事件流转成 SSE 对；收尾持久化 assistant 消息。永不抛。"""
    repo = repo or settings.default_repo
    cid: str = conversation_id or ""
    msg_id: int | None = None
    citations: list[dict] = []
    cost = CostController(max_tokens=settings.cost_max_tokens, max_llm_calls=settings.cost_max_llm_calls)
    try:
        conv, cid = await open_conversation(
            session, query=query, conversation_id=conversation_id, target_repo=repo)
        # R1（评审 F-A）：history 必须**先于**本测 user 落行读取——同事务 flush 即可读，
        # 后读会把当前 query 泄入 history，retrieve/react_base 的 seed 再追加一次 →
        # 连续两条相同 HumanMessage（每轮多烧一份 token 且干扰生成）。
        history = await load_history(session, cid, settings.history_turns)
        user_msg_id = await add_message(session, conv, role="user", content=query)
        yield ("conversation", {"conversation_id": cid, "title": conv.title,
                                "message_id": user_msg_id})

        config = {"configurable": {"session": session, "cost": cost, "top_k": top_k},
                  "recursion_limit": 60}
        state = {"query": query, "repo": repo, "conversation_id": cid, "history": history}

        tokens: list[str] = []
        agent_steps: list[dict] = []
        intent = ""
        route = ""
        async for chunk in GRAPH.astream(state, config=config, stream_mode="custom"):
            if not isinstance(chunk, dict) or "event" not in chunk:
                continue
            event, data = chunk["event"], chunk.get("data")
            yield (event, data)
            # 事件即数据：顺序无关累积（citation/agent_step 在 ReAct 路收尾 drain，晚于 token）
            if event == "token":
                content = data.get("content") if isinstance(data, dict) else None
                if isinstance(content, str):  # 空 chunk / list content 跳过
                    tokens.append(content)
            elif event == "citation":
                citations.append(data)
            elif event == "agent_step":
                agent_steps.append(data)
            elif event == "retrieval" and isinstance(data, dict):
                intent = data.get("intent") or intent
                route = data.get("mode") or route  # 降级路双 retrieval：按最后一条生效

        answer = "".join(tokens)
        msg_id = await add_message(
            session, conv, role="assistant", content=answer,
            meta={"citations": citations, "agent_steps": agent_steps,
                  "intent": intent, "route": route, "cost": cost.to_meta()})
        await session.commit()
        yield ("done", {"citations": len(citations), "message_id": msg_id, "conversation_id": cid})
    except Exception as e:  # noqa: BLE001 —— 兜底的兜底：HTTP 200 不断流
        logger.warning("stream_chat: 整体降级（conversation_id={}）: {}", cid, e)
        yield ("token", {"content": f"[内部错误: {type(e).__name__}]"})
        yield ("done", {"citations": len(citations), "message_id": msg_id, "conversation_id": cid})
