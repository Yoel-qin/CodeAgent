"""LLMJudge 单测：无 DB/无网络，注入 fake client。"""
from __future__ import annotations

import pytest

from app.eval.judge import QA_RUBRIC, LLMJudge


class _FakeLLM:
    def __init__(self, text: str, configured: bool = True):
        self._text = text
        self.configured = configured

    async def chat(self, messages, **kw):
        return self._text


@pytest.mark.asyncio
async def test_judge_parses_valid_json():
    judge = LLMJudge(client=_FakeLLM('{"faithfulness": 0.9, "answer_relevance": 0.8, '
                                     '"citation_accuracy": 0.7, "hallucination": 0.1, '
                                     '"rationale": "基本准确"}'))
    res = await judge.judge("Q", "A", "ctx", [], rubric=QA_RUBRIC)
    assert res.scores["faithfulness"].score == 0.9
    assert res.scores["hallucination"].score == 0.1
    assert res.rationale == "基本准确"


@pytest.mark.asyncio
async def test_judge_strips_json_fence():
    judge = LLMJudge(client=_FakeLLM('```json\n{"faithfulness": 1.0, "answer_relevance": 1.0, '
                                     '"citation_accuracy": 1.0, "hallucination": 0.0}\n```'))
    res = await judge.judge("Q", "A", "ctx", [], rubric=QA_RUBRIC)
    assert res.scores["faithfulness"].score == 1.0


@pytest.mark.asyncio
async def test_judge_invalid_json_degrades_to_none():
    judge = LLMJudge(client=_FakeLLM("这不是 JSON"))
    res = await judge.judge("Q", "A", "ctx", [], rubric=QA_RUBRIC)
    assert all(s.score is None for s in res.scores.values())
    assert "parse failed" in res.rationale


@pytest.mark.asyncio
async def test_judge_no_key_skips_request():
    judge = LLMJudge(client=_FakeLLM("x", configured=False))
    res = await judge.judge("Q", "A", "ctx", [], rubric=QA_RUBRIC)
    assert all(s.score is None for s in res.scores.values())
    assert "no llm key" in res.rationale


def test_judge_clips_scores_to_unit():
    """分数 clip 到 [0,1]；缺字段 -> None。"""
    # 缺 hallucination 字段 -> 该维 None，其余正常
    pass  # 见 Step 3 实现后补：用 _parse 直接测
