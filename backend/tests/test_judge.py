"""LLMJudge 单测：无 DB/无网络，注入 fake client。"""
from __future__ import annotations

import pytest

from app.eval.judge import QA_RUBRIC, LLMJudge, _parse


class _FakeLLM:
    def __init__(self, text: str, configured: bool = True):
        self._text = text
        self.configured = configured

    async def chat(self, messages, **kw):
        return self._text


class _ExplodingLLM:
    """chat() 总是抛异常，模拟网络故障。"""
    configured = True

    async def chat(self, messages, **kw):
        raise ConnectionError("timed out")


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


@pytest.mark.asyncio
async def test_judge_chat_exception_degrades():
    """网络异常（timeout 等）→ 全 None + 'llm call failed'。"""
    judge = LLMJudge(client=_ExplodingLLM())
    res = await judge.judge("Q", "A", "ctx", [], rubric=QA_RUBRIC)
    assert all(s.score is None for s in res.scores.values())
    assert "llm call failed" in res.rationale


@pytest.mark.asyncio
async def test_judge_non_dict_json_degrades():
    """LLM 返回合法 JSON 但非 dict（如数组）→ parse failed。"""
    judge = LLMJudge(client=_FakeLLM("[1, 2, 3]"))
    res = await judge.judge("Q", "A", "ctx", [], rubric=QA_RUBRIC)
    assert all(s.score is None for s in res.scores.values())
    assert "parse failed" in res.rationale


def test_judge_clips_scores_to_unit():
    """分数 clip 到 [0,1]；缺维度键 → None。"""
    # 过范围值：faithfulness=1.7 → clip 1.0，hallucination=-0.3 → clip 0.0
    res = _parse(
        '{"faithfulness": 1.7, "answer_relevance": 0.5, '
        '"citation_accuracy": 0.5, "hallucination": -0.3}',
        QA_RUBRIC,
    )
    assert res.scores["faithfulness"].score == 1.0
    assert res.scores["hallucination"].score == 0.0
    assert res.scores["answer_relevance"].score == 0.5


def test_judge_missing_dimension_key_is_none():
    """缺某维度键 → 该维 None，其余正常解析。"""
    # 缺 hallucination
    res = _parse(
        '{"faithfulness": 0.8, "answer_relevance": 0.9, "citation_accuracy": 0.7}',
        QA_RUBRIC,
    )
    assert res.scores["faithfulness"].score == 0.8
    assert res.scores["hallucination"].score is None
