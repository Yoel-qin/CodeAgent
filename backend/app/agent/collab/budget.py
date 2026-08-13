"""M35 协作预算纯函数 + 优雅收尾报告。

预算走 AgentState 计数器（``collab_llm_calls`` / ``collab_tool_calls``，operator.add），
**不引入可变 Budget 对象**——可序列化、可 checkpoint、与既有 reducer 风格一致。
每层节点入口用 ``remaining(已消耗, 上限)`` 算余量，传给 ``_bounded_tool_loop``。
"""
from __future__ import annotations


def remaining(used: int, cap: int) -> int:
    """剩余预算 = max(0, cap - used)。"""
    return max(0, cap - used)


def exhausted(left: int) -> bool:
    """余量 ≤ 0 即耗尽。"""
    return left <= 0


def build_collab_report(hypotheses: list[dict], findings: list[dict],
                        suggestions: list[dict]) -> str:
    """用已累积的 WorkingMemory 汇总诊断报告（预算耗尽 / 正常收尾共用）。

    三段：诊断假设 / 代码验证 / 调优建议。空输入 → 兜底文案（非空串，保证至少一条 token）。
    """
    lines: list[str] = []
    if hypotheses:
        lines.append("【诊断假设】")
        for i, h in enumerate(hypotheses, 1):
            lines.append(f"{i}. {h.get('hypothesis')}（置信度：{h.get('confidence', '中')}）")
    if findings:
        lines.append("【代码验证】")
        for f in findings:
            tag = "支持" if f.get("verdict") == "supports" else "反驳"
            lines.append(f"- [{tag}] {f.get('finding')}（chunk：{f.get('chunk_id', '?')}）")
    if suggestions:
        lines.append("【调优建议】")
        for s in suggestions:
            lines.append(f"- {s.get('suggestion')}")
    return "\n".join(lines) if lines else "（协作未能在检索结果中找到足够证据或预算耗尽，未产出诊断结论）"
