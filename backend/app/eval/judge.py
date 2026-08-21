"""通用 LLMJudge（M39）：rubric 驱动，单次 LLM 调用按 rubric 出 JSON 多维分 + 容错降级。

无 LLM key / JSON 解析失败 → 维度分 None + rationale 标原因，永不抛。M39 传 QA_RUBRIC
（4 维）；M40 diagnosis runner 传诊断 rubric（root_cause/...），同一函数。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.clients.llm_client import llm
from app.core.config import settings


@dataclass
class DimensionScore:
    score: float | None
    weight: float = 1.0


@dataclass
class JudgeResult:
    scores: dict[str, DimensionScore]
    rationale: str
    raw: str


def _extract_json_object(text: str) -> str | None:
    """提取首个平衡大括号块（字符串感知——跳过字符串字面量里的 { } 与转义）。

    覆盖轻量模型（如 deepseek-v4-flash）的噪声形态：JSON 前后包自然语言、
    fence 混排。无完整平衡块（截断）→ None。
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _parse(raw: str, rubric: dict) -> JudgeResult:
    """解析 LLM 文本 → JudgeResult。

    快路径整体 json.loads → 失败再大括号平衡提取（M46 容错增强）→ 仍失败 → 全 None。
    """
    scores = {k: DimensionScore(None, cfg["weight"]) for k, cfg in rubric.items()}
    text = (raw or "").strip()
    obj = None
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        frag = _extract_json_object(text)
        if frag:
            try:
                obj = json.loads(frag)
            except (json.JSONDecodeError, TypeError):
                obj = None
    if not isinstance(obj, dict):
        return JudgeResult(scores=scores, rationale="judge parse failed", raw=raw)
    rationale = str(obj.get("rationale", ""))
    for k in rubric:
        if k in obj and obj[k] is not None:
            try:
                v = float(obj[k])
                scores[k] = DimensionScore(round(min(max(v, 0.0), 1.0), 4), rubric[k]["weight"])
            except (TypeError, ValueError):
                pass
    return JudgeResult(scores=scores, rationale=rationale, raw=raw)


def _build_prompt(question, answer, context, citations, rubric, *, scoring_hints: dict | None = None) -> list[dict]:
    dims = "\n".join(f"- {k}（{cfg['direction']}，0-1）：{cfg['desc']}" for k, cfg in rubric.items())
    keys = list(rubric.keys())
    fmt_keys = ", ".join(f'"{k}": <0-1分数>' for k in keys)
    sys_msg = (
        "你是严格的代码问答质量评审员。按下列 rubric 维度给分，只输出一个 JSON 对象，"
        f"不要任何额外文本。维度：\n{dims}\n\n输出格式：{{{fmt_keys}, \"rationale\": \"中文简述主要扣分点\"}}。"
    )
    cit_blob = "\n".join(f"- {c.get('chunk_id')}: {c.get('label')}" for c in citations[:10]) or "（无引用）"
    user_msg = (
        f"=== 问题 ===\n{question}\n\n=== 检索到的 context ===\n{context[:3000]}\n\n"
        f"=== 引用 ===\n{cit_blob}\n\n=== 待评回答 ===\n{answer[:2000]}"
    )
    # 评分锚点(spec §10):QA 的 should_mention/should_not_hallucinate + M40 诊断三元组注入为评分锚点,跳过空子表。
    if scoring_hints:
        anchor_lines: list[str] = []
        sm = scoring_hints.get("should_mention") or []
        if sm:
            anchor_lines.append(f"期望提及: {', '.join(map(str, sm))}")
        snh = scoring_hints.get("should_not_hallucinate") or []
        if snh:
            anchor_lines.append(f"不应捏造: {', '.join(map(str, snh))}")
        # M40 诊断锚点:expected 三元组(与 QA keys 并列,空列表跳过)
        rch = scoring_hints.get("root_cause_hints") or []
        if rch:
            anchor_lines.append(f"根因提示: {', '.join(map(str, rch))}")
        rc = scoring_hints.get("relevant_code") or []
        if rc:
            anchor_lines.append(f"相关代码: {', '.join(map(str, rc))}")
        cs = scoring_hints.get("config_suggestions") or []
        if cs:
            anchor_lines.append(f"配置建议: {', '.join(map(str, cs))}")
        if anchor_lines:
            user_msg += "\n\n=== 评分锚点 ===\n" + "\n".join(anchor_lines)
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]


class LLMJudge:
    def __init__(self, *, client=None, model: str | None = None):
        self._client = client or llm
        self._model = model or settings.judge_model or None

    async def judge(
        self, question, answer, context, citations, *, rubric: dict, scoring_hints: dict | None = None
    ) -> JudgeResult:
        if not self._client.configured:
            scores = {k: DimensionScore(None, cfg["weight"]) for k, cfg in rubric.items()}
            return JudgeResult(scores=scores, rationale="no llm key", raw="")
        kw = {}
        if self._model:
            kw["model"] = self._model
        msgs = _build_prompt(question, answer, context, citations, rubric, scoring_hints=scoring_hints)
        try:
            raw = await self._client.chat(msgs, temperature=0, max_tokens=1024, **kw)
        except Exception as exc:
            scores = {k: DimensionScore(None, cfg["weight"]) for k, cfg in rubric.items()}
            return JudgeResult(scores=scores, rationale=f"llm call failed: {exc}", raw="")
        result = _parse(raw, rubric)
        if all(s.score is None for s in result.scores.values()):
            # M46：轻量模型易夹带噪声/拒答 JSON → 强化指令重试一次（eval 门的意义大于一次额外调用）
            try:
                raw2 = await self._client.chat(
                    [*msgs, {"role": "assistant", "content": raw or ""},
                     {"role": "user", "content": "上一次输出无法解析为 JSON。请重新输出：只给一个 JSON 对象，"
                                                 "无任何解释文字、无 markdown fence。"}],
                    temperature=0, max_tokens=1024, **kw,
                )
                retry = _parse(raw2, rubric)
                if any(s.score is not None for s in retry.scores.values()):
                    return retry
            except Exception:
                pass
        return result


# M39 QA rubric（4 维；unverified_rate 不在此，由 M34 enforce 算）
QA_RUBRIC: dict = {
    "faithfulness": {"desc": "回答中的陈述是否都能由检索到的 context 支持。1=完全支持，0=大量无依据。", "direction": "high_good", "weight": 1.0},
    "answer_relevance": {"desc": "回答是否直接切题回答了问题。1=完全切题，0=答非所问。", "direction": "high_good", "weight": 1.0},
    "citation_accuracy": {"desc": "回答中标注的引用（chunk_id/类名.方法名）是否与提供的 citations 一致且准确。1=全准确，0=引用错误或编造。", "direction": "high_good", "weight": 1.0},
    "hallucination": {"desc": "回答是否包含 context 中不存在的捏造信息（代码标识符/行为/配置）。1=严重捏造，0=无捏造（低=好）。", "direction": "low_bad", "weight": 1.0},
}
