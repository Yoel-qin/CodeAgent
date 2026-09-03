"""SpanCollector：请求级 span 树采集器（M7 Task 8，零 IO 纯件）。

持久化归 Task 9 的 streaming 层（assistant 落行同一事务写 ``trace_spans``）；
本模块只构造普通 dict——不 import SQLAlchemy、不做任何 I/O，便于单测与任意层复用。

span 字典形状冻结（``trace_spans.spans`` JSONB 平面列表元素，前端 TraceView 直接消费）::

    {"span_id": int, "parent_id": int|null, "kind": str, "name": str,
     "start_ms": float, "duration_ms": float|null, "status": "ok"|"error",
     "error": str|null, "tokens": {"prompt": int, "completion": int,
     "estimated": bool}|null, "attrs": {}}

``start_ms``/``duration_ms`` 均相对采集器创建时刻（time.perf_counter 单调钟，取 0.1ms）。
"""
from __future__ import annotations

import itertools
import time


class SpanCollector:
    """请求级 span 树采集器（M7，移植旧库 M41 模式的 v2 精简版）。

    经 ``config["configurable"]["trace"]`` 注入（同 cost 模式，不进图状态/checkpoint）。
    span 字典形状 = v1 TraceSpan 平面形状（见 Plan Global Constraints）——TraceView
    直接消费；``start_ms`` 相对本采集器创建时刻。
    """

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self._ids = itertools.count(1)
        self.spans: list[dict] = []
        self._open: dict[int, dict] = {}

    def start(self, kind: str, name: str, parent_id: int | None = None,
              attrs: dict | None = None) -> int:
        """开一个 span（返回 span_id）；kind ∈ request/route/agent/tool/llm/retrieval。"""
        span_id = next(self._ids)
        self._open[span_id] = {
            "span_id": span_id, "parent_id": parent_id, "kind": kind, "name": name,
            "start_ms": round((time.perf_counter() - self._t0) * 1000, 1),
            "duration_ms": None, "status": "ok", "error": None, "tokens": None,
            "attrs": dict(attrs or {}),
        }
        return span_id

    def end(self, span_id: int, *, status: str = "ok", error: str | None = None,
            tokens: dict | None = None, attrs: dict | None = None) -> None:
        """关一个 span（记录 duration）；重复 end / 未知 id 静默忽略（旁观者契约）。"""
        span = self._open.pop(span_id, None)
        if span is None:
            return
        if attrs:
            span["attrs"].update(attrs)
        span["duration_ms"] = round((time.perf_counter() - self._t0) * 1000, 1) - span["start_ms"]
        span["status"] = status
        span["error"] = error
        span["tokens"] = tokens
        self.spans.append(span)

    def to_dict(self) -> list[dict]:
        """已关闭的 span 列表（按 end 序；未关闭的丢弃——请求收尾调用）。"""
        return [dict(s) for s in self.spans]
