"""ReAct 骨架的两只回调（Plan 3 Task 8，语义沿旧库 ``app/agent/llm.py`` 同名 handler）。

- :class:`TokenSSEHandler`：捕获模型流式 token → ``writer({"event": "token", ...})``（writer
  构造注入——v2 由 react_base 用 ``_safe_writer()`` 的主图流 writer 注入，不再回调内取上下文）。
  **非流式回退**：langchain-core 只在模型真走流式（``ChatOpenAI(streaming=True)``）时才回调
  ``on_llm_new_token``（普通 handler 不会触发流式，测试 stub / 关流式端点一次 token 都不给）；
  故 ``on_llm_end`` 按次结算——该次调用一个 token 都没见过 → 整段文本一次性补发单个 token 事件，
  避免非流式模型静默无输出。中间工具决策轮 content 为空，天然不触发。
- :class:`CostCallbackHandler`：把 LLM 调用次数/usage 记入 :class:`~app.agent.cost.CostController`
  （只记不抛——langchain 吞回调异常）。usage 取 ``usage_metadata``（prompt/completion 真值）；
  缺失 → 按文本 ``chars//4`` 估算（completion 记账、prompt 记 0）并标 ``estimated=True``。
  拦截不在回调里做：由 react_base 的 astream chunk 循环轮询 ``cost.exceeded``。
  M7 起可选 ``trace=SpanCollector``：每次 LLM 调用一个 ``llm`` span（start 记
  ``on_chat_model_start``、end 带与记账同口径的 ``tokens`` dict）——``trace=None``（缺省，
  亦是既有全部不带 trace 的构造点）零行为变更。

任何回调异常一律静默（旁观者契约，绝不影响请求）。
"""
from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

__all__ = ["CostCallbackHandler", "TokenSSEHandler"]


class TokenSSEHandler(BaseCallbackHandler):
    """模型流式 token → SSE ``token`` 事件；非流式调用在 on_llm_end 一次性补发全文。"""

    def __init__(self, writer) -> None:
        self.writer = writer
        self._tokens_by_run: dict[Any, int] = {}   # run_id → 已流式 token 数（按次结算用）

    def _emit(self, content: str) -> None:
        try:
            if self.writer is not None:
                self.writer({"event": "token", "data": {"content": content}})
        except Exception:  # noqa: BLE001 —— 旁观者契约
            pass

    def on_llm_new_token(self, token, *, run_id=None, **kwargs) -> None:  # noqa: ARG002
        if not isinstance(token, str) or not token:   # v2 全链 ChatOpenAI → 恒为 str 增量
            return
        try:
            self._emit(token)
            if run_id is not None:
                self._tokens_by_run[run_id] = self._tokens_by_run.get(run_id, 0) + 1
        except Exception:  # noqa: BLE001
            pass

    def on_llm_end(self, response, *, run_id=None, **kwargs) -> None:  # noqa: ARG002
        """非流式回退：该次调用没流过一个 token → 整段文本补发单个 token 事件。"""
        try:
            if self._tokens_by_run.pop(run_id, 0) > 0:
                return
            for gens in (getattr(response, "generations", None) or []):
                for g in gens or []:
                    text = (getattr(g, "text", "") or "")
                    if not text and getattr(g, "message", None) is not None:
                        text = str(getattr(g.message, "content", "") or "")
                    if text:
                        self._emit(text)
        except Exception:  # noqa: BLE001
            pass


class CostCallbackHandler(BaseCallbackHandler):
    """LLM 调用次数/usage → CostController（只记不抛，语义沿旧库 M42）。

    ``trace``（M7 可选）：非 None 时每次 LLM 调用记一个 ``llm`` span——
    ``on_chat_model_start`` 开 span（按 ``run_id`` 暂存）、``on_llm_end`` 关 span 并带
    ``tokens``（与记账同口径：usage 真值 ``estimated=False``，否则 chars/4 估算
    ``estimated=True``）。trace 缺席（None）→ 完全不碰 span，零行为变更。
    """

    def __init__(self, cost, trace=None) -> None:
        self.cost = cost
        self.trace = trace
        self._span_by_run: dict[Any, int] = {}   # run_id → llm span_id

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs) -> None:  # noqa: ARG002
        try:
            self.cost.record_call()
            if self.trace is not None:
                self._span_by_run[run_id] = self.trace.start("llm", "llm")
        except Exception:  # noqa: BLE001
            pass

    def on_llm_end(self, response, *, run_id=None, **kwargs) -> None:  # noqa: ARG002
        try:
            usage = self._usage_from_response(response)
            if usage is not None:
                self.cost.record_usage(prompt=usage[0], completion=usage[1])
                tokens = {"prompt": usage[0], "completion": usage[1], "estimated": False}
            else:
                text = self._response_text(response)
                est = len(text) // 4   # 无 usage → chars/4 估算（prompt 记 0，诚实标 estimated）
                if text:
                    self.cost.record_usage(prompt=0, completion=est, estimated=True)
                tokens = {"prompt": 0, "completion": est, "estimated": True}
            if self.trace is not None:
                sid = self._span_by_run.pop(run_id, None)
                if sid is not None:   # 无对应 start（中途挂上的回调等）→ 静默跳过
                    self.trace.end(sid, tokens=tokens)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _usage_from_response(response) -> tuple[int, int] | None:
        """``generations[0][0].message.usage_metadata`` 的 (prompt, completion)；缺失 → None。"""
        try:
            msg = response.generations[0][0].message
            meta = getattr(msg, "usage_metadata", None) or {}
            if "prompt_tokens" in meta or "completion_tokens" in meta:
                return int(meta.get("prompt_tokens") or 0), int(meta.get("completion_tokens") or 0)
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _response_text(response) -> str:
        """全 generation 文本拼接（估算口径）。"""
        parts: list[str] = []
        for gens in (getattr(response, "generations", None) or []):
            for g in gens or []:
                parts.append(getattr(g, "text", "") or "")
        return "".join(parts)
