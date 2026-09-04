"""评测 harness（M8）：直接 ``GRAPH.astream`` 驱动主图，逐 case 收集事件证据。

与生产 ``stream_chat`` 的差异（设计决策）：**不走持久化链**——不落
conversations/chat_messages/trace_spans（评测流量零污染业务表），答案/引用/轮次/路由
全部从 custom 事件流收集，token 用量取 per-case ``CostController.to_meta()``；
每 case 独立（history 空、无 conversation_id），变体旋钮经 ``configurable`` 注入
（Task 3 接缝），reasoning 档模型覆盖经 ContextVar 包裹 apply/reset。

brief 适配（有据偏差，须带入评审）：``build_row`` 的 ``unresolved`` 不按 brief 实现的
「targets dict 键里空列表」收集——brief 逐字测试传入 ``code_targets`` **不含** ``Ghost.m``
键、``doc_targets={}``，却断言两者都进 ``unresolved``。故改为按 ``case.expect_code`` /
``expect_doc`` spec 逐个查 targets dict（缺键或空列表 = unresolved），与逐字测试一致；
``has_*_anchor`` 仍只看目标非空（unresolved spec 不进命中率分母）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.agent.cost import CostController
from app.agent.graph import GRAPH
from app.clients.llm import apply_model_overrides, reset_model_overrides
from app.core.config import settings
from app.eval import match
from app.eval.golden import CodeTarget, DocTarget, GoldenCase

__all__ = ["CaseEvidence", "EvalVariant", "build_row", "run_case"]


@dataclass
class EvalVariant:
    """一个评测变体。全字段缺席 = 生产默认（零行为变更）；baseline = 全默认。"""

    name: str = "baseline"
    rounds_code: int | None = None
    rounds_doc: int | None = None
    code_no_graph: bool = False
    model_reasoning: str | None = None   # reasoning 档 model 名覆盖（其余端点字段继承）
    top_k: int | None = None

    def overrides(self) -> dict:
        """→ configurable 增量键值（缺席键不出现 = 生产默认）。"""
        out: dict = {}
        if self.rounds_code is not None:
            out["rounds_code"] = self.rounds_code
        if self.rounds_doc is not None:
            out["rounds_doc"] = self.rounds_doc
        if self.code_no_graph:
            out["code_no_graph"] = True
        return out

    def model_overrides(self) -> dict:
        return {"reasoning": {"model": self.model_reasoning}} if self.model_reasoning else {}


@dataclass
class CaseEvidence:
    """一条 case 一条证据流（全部从 custom 事件收集，无 DB 写）。"""

    case_id: str
    variant: str
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    agent_steps: list[dict] = field(default_factory=list)
    route: str = ""
    duration_ms: float = 0.0
    token_usage: dict | None = None


def _build_config(variant: EvalVariant, cost: CostController) -> dict:
    """组装 per-case config（与 stream_chat 同构：cost/top_k 走 configurable + 旋钮增量）。"""
    return {
        "configurable": {"cost": cost, "top_k": variant.top_k or 8, **variant.overrides()},
        "recursion_limit": 60,
    }


async def run_case(case: GoldenCase, variant: EvalVariant) -> CaseEvidence:
    """跑一条 case：驱动主图收集证据。永不写业务表；图内兜底链保证永不抛。"""
    cost = CostController(max_tokens=settings.cost_max_tokens,
                          max_llm_calls=settings.cost_max_llm_calls)
    config = _build_config(variant, cost)
    state = {"query": case.query, "repo": case.repo, "conversation_id": "", "history": []}
    ev = CaseEvidence(case_id=case.id, variant=variant.name)
    token = apply_model_overrides(variant.model_overrides())
    t0 = time.perf_counter()
    try:
        async for chunk in GRAPH.astream(state, config=config, stream_mode="custom"):
            if not isinstance(chunk, dict) or "event" not in chunk:
                continue
            event, data = chunk["event"], chunk.get("data")
            if event == "token":
                content = data.get("content") if isinstance(data, dict) else None
                if isinstance(content, str):
                    ev.answer += content
            elif event == "citation" and isinstance(data, dict):
                ev.citations.append(data)
            elif event == "agent_step" and isinstance(data, dict):
                ev.agent_steps.append(data)
            elif event == "retrieval" and isinstance(data, dict):
                ev.route = data.get("mode") or ev.route  # 降级路双 retrieval：取最后一条
    finally:
        reset_model_overrides(token)
    ev.duration_ms = (time.perf_counter() - t0) * 1000
    ev.token_usage = cost.to_meta()
    return ev


def build_row(case: GoldenCase, evidence: CaseEvidence,
              code_targets: dict[str, list[CodeTarget]],
              doc_targets: dict[str, list[DocTarget]]) -> dict:
    """证据 + 已解析目标 → per_query 冻结行（metrics.aggregate 的输入形状）。

    ``code_targets``/``doc_targets`` 键 = 锚点 spec；spec 缺键或目标空列表 = unresolved，
    记入行内 ``unresolved`` 且不参与命中率分母（``has_*_anchor`` 只看目标非空）。
    """
    all_code = [t for ts in code_targets.values() for t in ts]
    all_doc = [t for ts in doc_targets.values() for t in ts]
    m = match.match_case(evidence.citations, all_code, all_doc)
    tu = evidence.token_usage or {}
    return {
        "case_id": case.id,
        "variant": evidence.variant,
        "hit_code": m["hit_code"],
        "hit_doc": m["hit_doc"],
        "has_code_anchor": bool(all_code),
        "has_doc_anchor": bool(all_doc),
        "matched": m["matched"],
        "total": m["total"],
        "precision": (m["matched"] / m["total"]) if m["total"] else None,
        "rounds": len(evidence.agent_steps),
        "latency_ms": round(evidence.duration_ms, 1),
        "tokens": tu.get("spent_tokens"),
        "llm_calls": tu.get("llm_calls"),
        "route": evidence.route,
        "answer_chars": len(evidence.answer),
        "unresolved": [s for s in case.expect_code if not code_targets.get(s)]
                      + [s for s in case.expect_doc if not doc_targets.get(s)],
    }
