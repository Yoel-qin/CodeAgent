"""请求级结构化 Trace（M41）：intent→route→agent→retrieval→tool→llm 各阶段记为
带 parent/duration/token 的 span 树，序列化进 ``retrieval_logs.agent_steps``
JSONB（``{"version":2,...}`` dict 形状，零迁移）。

设计（spec docs/superpowers/specs/2026-08-17-m41-structured-trace-design.md）：
- **旁观者契约**：span 记录异常（status=error）后重抛；序列化失败返回空 spans；
  trace 绝不影响主流程/既有降级行为。
- 双计时模式：``span()`` 上下文管理器（顺序嵌套，栈维护 parent）；
  ``start()/end()`` 手动（asyncio.gather 并发/回调场景，显式 parent_id）；
  ``record()`` 即时叶 span（tools/route，外部已计时）。并发下各 span 独立计时。
- token：真值（llm usage）优先，缺失按文本长度 /4 估算（与 monitor api-usage 公式
  一致）并标 ``estimated:true``；summary.tokens 为各 llm span 求和，
  ``estimated = 任一来源为估算``。
- collector 仅在事件循环单线程内使用，不加锁。
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass, field


@dataclass
class Span:
    span_id: int
    parent_id: int | None
    kind: str  # request|intent|route|agent|collab|tool|retrieval|llm|degrade
    name: str
    start_ms: float
    duration_ms: float | None = None
    status: str = "ok"
    error: str | None = None
    tokens: dict | None = None
    attrs: dict = field(default_factory=dict)


def tokens_from_usage(usage: dict | None, *, prompt_chars: int = 0,
                      completion_chars: int = 0) -> dict:
    """真值优先：``usage={"prompt_tokens","completion_tokens"}`` → estimated=False；
    缺失 → chars/4 估算 → estimated=True。"""
    if usage and usage.get("prompt_tokens") is not None:
        return {"prompt": int(usage["prompt_tokens"]),
                "completion": int(usage.get("completion_tokens") or 0),
                "estimated": False}
    return {"prompt": prompt_chars // 4, "completion": completion_chars // 4,
            "estimated": True}


class SpanCollector:
    """请求级 span 收集器；``t0`` = 构造时刻（perf_counter 单调时钟）。"""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self._next_id = 0
        self._spans: list[Span] = []
        self._stack: list[int] = []  # 仅 span() 上下文管理器维护

    @property
    def stack_top(self) -> int | None:
        return self._stack[-1] if self._stack else None

    def _now_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 2)

    def start(self, kind: str, name: str, *, parent_id: int | None = None,
              attrs: dict | None = None) -> Span:
        """手动开 span（并发/回调场景；**不进栈**，调用方负责 end()）。"""
        self._next_id += 1
        s = Span(span_id=self._next_id, parent_id=parent_id, kind=kind, name=name,
                 start_ms=self._now_ms(), attrs=dict(attrs or {}))
        self._spans.append(s)
        return s

    def end(self, span: Span, *, error: str | None = None,
            tokens: dict | None = None) -> None:
        span.duration_ms = round(self._now_ms() - span.start_ms, 2)
        if error:
            span.status, span.error = "error", error[:200]
        if tokens:
            span.tokens = tokens

    @contextmanager
    def span(self, kind: str, name: str, **attrs):
        """顺序嵌套语法糖：parent=栈顶；异常记 error 后**重抛**（旁观者契约）。"""
        s = self.start(kind, name, parent_id=self.stack_top, attrs=attrs or None)
        self._stack.append(s.span_id)
        try:
            yield s
        except Exception as e:  # noqa: BLE001
            self.end(s, error=f"{type(e).__name__}: {e}")
            raise
        else:
            self.end(s)
        finally:
            if self._stack and self._stack[-1] == s.span_id:
                self._stack.pop()

    def record(self, kind: str, name: str, duration_ms: float, *,
               parent_id: int | None = None, attrs: dict | None = None,
               tokens: dict | None = None) -> None:
        """即时叶 span（tools/route 等：时长已在外部测好）。"""
        self._next_id += 1
        self._spans.append(Span(
            span_id=self._next_id, parent_id=parent_id, kind=kind, name=name,
            start_ms=round(self._now_ms() - duration_ms, 2),
            duration_ms=round(float(duration_ms), 2),
            tokens=tokens, attrs=dict(attrs or {}),
        ))

    def to_payload(self) -> dict:
        """序列化；任何异常 → 空 spans（trace 不影响落库主流程）。"""
        try:
            spans = [asdict(s) for s in self._spans]
            llm_spans = [s for s in self._spans if s.kind == "llm"]
            prompt = sum((s.tokens or {}).get("prompt") or 0 for s in llm_spans)
            completion = sum((s.tokens or {}).get("completion") or 0 for s in llm_spans)
            estimated = any((s.tokens or {}).get("estimated") for s in llm_spans if s.tokens)
            total = max((s.start_ms + (s.duration_ms or 0)) for s in self._spans) \
                if self._spans else 0.0
            return {
                "version": 2,
                "spans": spans,
                "summary": {
                    "total_ms": round(total, 2),
                    "tokens": {"prompt": prompt, "completion": completion,
                               "n_llm_calls": len(llm_spans), "estimated": estimated},
                    "n_spans": len(spans),
                    "kind_counts": {k: sum(1 for s in self._spans if s.kind == k)
                                    for k in sorted({s.kind for s in self._spans})},
                },
            }
        except Exception:  # noqa: BLE001
            return {"version": 2, "spans": [],
                    "summary": {"total_ms": 0.0,
                                "tokens": {"prompt": 0, "completion": 0,
                                           "n_llm_calls": 0, "estimated": False},
                                "n_spans": 0, "kind_counts": {}}}


class _LLMSpanCtx:
    """llm_span 的 yield 对象：usage_out 供调用方填真值；add_token 累计流式 chars。"""

    def __init__(self) -> None:
        self.usage_out: dict = {}
        self.completion_chars = 0
        self._error: str | None = None

    def add_token(self, tok: str) -> None:
        self.completion_chars += len(tok or "")

    def mark_error(self, exc: BaseException) -> None:
        """调用方在 with 块内捕获异常后调用，标记此 span 为 error（旁观者契约：不抛）。"""
        self._error = f"{type(exc).__name__}: {exc}"[:200]


@asynccontextmanager
async def llm_span(collector: SpanCollector | None, name: str,
                   prompt_text: str = "", *, parent_id: int | None = None):
    """包一次流式 LLM 调用：进入开 llm span（parent=栈顶或显式 parent_id）；退出按
    usage_out 真值 / chars 估算结算 tokens。collector=None → 零开销直通。
    异常处理：调用方 mark_error() → error 记入 span；未捕获异常 → except 记 error 后重抛。"""
    ctx = _LLMSpanCtx()
    s = collector.start("llm", name,
                        parent_id=parent_id if parent_id is not None else collector.stack_top) \
        if collector is not None else None
    try:
        yield ctx
    except Exception as e:  # noqa: BLE001
        if s is not None:
            ctx.mark_error(e)
        raise
    finally:
        if s is not None:
            collector.end(s, error=ctx._error, tokens=tokens_from_usage(
                ctx.usage_out or None,
                prompt_chars=len(prompt_text),
                completion_chars=ctx.completion_chars))
