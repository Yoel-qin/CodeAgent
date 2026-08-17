"""生成节点：组装上下文 → LLM 流式生成（复用 chat_service.build_context/build_messages + llm）。

逐 token 通过 get_stream_writer 推 token 事件；未配置 Key / 调用失败优雅降级（同 legacy）。
M41：config 含 trace 时用 llm_span 包流式（usage 真值优先 + token 累计）。
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agent.state import AgentState
from app.agent.trace import llm_span
from app.clients.llm_client import llm
from app.services.chat_service import _no_key_notice, build_context, build_messages


async def generate(state: AgentState, config: RunnableConfig) -> dict:
    query = state["query"]
    agent_type = config["configurable"].get("agent_type")
    meta = state.get("retrieval_meta", {})
    context = build_context(state.get("ranked", []))
    collector = config["configurable"].get("trace")

    writer = get_stream_writer()
    parts: list[str] = []
    if llm.configured:
        messages = build_messages(query, context, agent_type, state.get("history", []))
        async with llm_span(collector, llm.model, prompt_text=context) as ls:
            try:
                async for tok in llm.stream_tokens(messages, usage_out=ls.usage_out):
                    ls.add_token(tok)
                    parts.append(tok)
                    writer({"event": "token", "data": {"content": tok}})
            except Exception as e:  # noqa: BLE001
                msg = f"\n[LLM 调用失败：{type(e).__name__}: {e}]"
                parts.append(msg)
                writer({"event": "token", "data": {"content": msg}})
        if not parts:
            fallback = "（模型未返回内容）"
            parts.append(fallback)
            writer({"event": "token", "data": {"content": fallback}})
    else:
        notice = _no_key_notice(meta)
        parts.append(notice)
        writer({"event": "token", "data": {"content": notice}})

    return {"answer": "".join(parts), "context": context}
