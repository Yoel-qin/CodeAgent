"""M42 CostController：per-request 预算双闸（纯 Python，零框架依赖）。

- token 闸：prompt+completion 合计（M41 usage 真值优先；估算计入并标 estimated）
- 调用数闸：LLM 调用次数
- 超限语义：``record_*`` 只标记不抛（langchain 会吞回调异常，回调侧抛了也白抛）；
  ``check()`` 在显式调用点（collab 每轮 / 生成前 / Agent chunk 循环）抛 BudgetExceeded
  → 进既有降级出口（_base 模板降级 / collab 优雅停），请求永不中断。
开关 off → ``make_cost_controller()`` 返 None，全部接缝零开销跳过。
"""
from __future__ import annotations

from app.core.config import settings


class BudgetExceeded(Exception):
    """预算超限。携带结构化信息供模板降级文案与 retrieval_meta.cost 记录。"""

    def __init__(self, reason: str, *, spent: int, cap: int) -> None:
        super().__init__(f"budget exceeded: {reason} (spent={spent}, cap={cap})")
        self.reason = reason
        self.spent = spent
        self.cap = cap

    def notice(self) -> str:
        """降级模板文案（超预算后不再烧 LLM 生成提示语）。"""
        return (f"\n[已达预算上限：{self.reason}（{self.spent}/{self.cap}），"
                "本次回答由降级模板提供，以上检索结果供参考]")


class CostController:
    """单请求预算账本。mark 侧（record_*）绝不抛；check() 才抛。"""

    def __init__(self, *, max_tokens: int, max_llm_calls: int) -> None:
        self.max_tokens = max_tokens
        self.max_llm_calls = max_llm_calls
        self.spent_tokens = 0
        self.llm_calls = 0
        self.estimated = False            # 任一笔为估算则 True（诚实标记，M41 同款）
        self.exceeded: BudgetExceeded | None = None   # 首次超限原因；chunk 循环轮询此字段

    def _mark(self, reason: str, *, spent: int, cap: int) -> None:
        if self.exceeded is None:
            self.exceeded = BudgetExceeded(reason, spent=spent, cap=cap)

    def record_call(self) -> None:
        """一次 LLM 调用开始（on_llm_start）。超调用数闸只标记。"""
        self.llm_calls += 1
        if self.llm_calls > self.max_llm_calls:
            self._mark("llm_calls", spent=self.llm_calls, cap=self.max_llm_calls)

    def record_usage(self, *, prompt: int = 0, completion: int = 0,
                     estimated: bool = False) -> None:
        """一次 LLM 调用结算（usage 真值或估算）。超 token 闸只标记。"""
        self.spent_tokens += prompt + completion
        self.estimated = self.estimated or estimated
        if self.spent_tokens > self.max_tokens:
            self._mark("tokens", spent=self.spent_tokens, cap=self.max_tokens)

    def check(self) -> None:
        """显式调用点用：已超限则抛 BudgetExceeded（进既有 except 降级出口）。"""
        if self.exceeded is not None:
            raise self.exceeded

    def to_meta(self) -> dict:
        """写进 retrieval_meta["cost"]（JSONB 零迁移）。"""
        return {
            "enabled": True,
            "spent_tokens": self.spent_tokens,
            "estimated": self.estimated,
            "llm_calls": self.llm_calls,
            "cap_tokens": self.max_tokens,
            "cap_llm_calls": self.max_llm_calls,
            "exceeded": self.exceeded.reason if self.exceeded else None,
        }


def make_cost_controller() -> CostController | None:
    """工厂：开关 off → None（零开销零行为变更，同 SpanCollector 之外的 opt-in 惯例）。"""
    if not settings.cost_control_enabled:
        return None
    return CostController(max_tokens=settings.cost_max_tokens_per_request,
                          max_llm_calls=settings.cost_max_llm_calls)
