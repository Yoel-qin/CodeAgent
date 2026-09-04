"""QA LLM 评判（M8）——旧库 M39 LLMJudge 思路移植：单次 reasoning 档调用，固定
4 维 rubric → JSON 分数（0..1，clamp），解析失败/缺维/无 key/异常 → None（软失败，
评测主指标不受影响）。维度语义：hallucination = 幻觉程度（**低 = 好**），报告原样呈现。
"""
from __future__ import annotations

import asyncio
import json
import re
from statistics import fmean

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from app.clients.llm import chat_model_for, configured

__all__ = ["JUDGE_DIMS", "judge_case", "judge_scores"]

JUDGE_DIMS = ("faithfulness", "answer_relevance", "citation_accuracy", "hallucination")

_JUDGE_TIMEOUT_S = 60.0

_JUDGE_SYSTEM = (
    "你是代码知识库问答系统的质量评判员。给定【问题】【系统回答】【引用列表】，"
    "对回答按 4 个维度各打 0 到 1 的分，只输出一个 json 对象（不要解释、不要围栏）：\n"
    "- faithfulness：回答是否忠于引用材料与问题，未引入无据断言（1=完全忠实）\n"
    "- answer_relevance：回答是否切题解决了问题（1=完全切题）\n"
    "- citation_accuracy：回答中提及的代码/文档位置是否与引用列表一致（无引用需求时 1）\n"
    "- hallucination：幻觉程度——编造的路径/行号/类名/机制描述有多少（0=无幻觉，**低=好**）"
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _user_prompt(query: str, answer: str, citations: list[dict]) -> str:
    lines = [f"【问题】{query}", f"【系统回答】{answer or '（空回答）'}", "【引用列表】"]
    for c in citations[:20]:
        if c.get("kind") == "code":
            lines.append(f"- {c.get('file_path')}:{c.get('start_line')} {c.get('label', '')}")
        elif c.get("kind") == "doc":
            lines.append(f"- {c.get('doc_id')}#{c.get('section')} {c.get('label', '')}")
    if not citations:
        lines.append("（无引用）")
    return "\n".join(lines)


def _parse_scores(text: str) -> dict | None:
    """容错解析：剥 ```json 围栏 → json.loads → 4 维齐 + 数值 clamp 0..1；任一不满足 → None。"""
    raw = text.strip()
    if not raw.startswith("{"):
        m = _FENCE_RE.search(raw)
        raw = m.group(1) if m else ""
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    out: dict[str, float] = {}
    for dim in JUDGE_DIMS:
        v = obj.get(dim)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return None
        out[dim] = min(1.0, max(0.0, float(v)))
    return out


async def judge_case(query: str, answer: str, citations: list[dict]) -> dict | None:
    """评一条：单次 LLM 调用（同步 invoke 经 to_thread + 60s 超时）；软失败 None。"""
    if not configured():
        return None
    try:
        model = chat_model_for("reasoning")
        messages = [SystemMessage(content=_JUDGE_SYSTEM),
                    HumanMessage(content=_user_prompt(query, answer, citations))]
        resp = await asyncio.wait_for(asyncio.to_thread(model.invoke, messages), _JUDGE_TIMEOUT_S)
        return _parse_scores(getattr(resp, "content", "") or "")
    except Exception as e:  # noqa: BLE001 —— 评判失败不破评测主指标
        logger.warning("judge: 评判失败软跳过: {}", e)
        return None


def judge_scores(rows: list[dict | None]) -> dict | None:
    """多 case 宏观平均（各维独立 fmean，跳过 None 行）；全部 None → None。"""
    out: dict[str, float] = {}
    for dim in JUDGE_DIMS:
        vals = [r[dim] for r in rows if r is not None]
        if vals:
            out[dim] = fmean(vals)
    return out or None
