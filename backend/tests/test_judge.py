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


class _CapturingLLM:
    """捕获发往 LLM 的 messages，供断言评分锚点是否注入。"""
    configured = True

    def __init__(self, text: str = '{"faithfulness": 1.0}'):
        self._text = text
        self.captured = None

    async def chat(self, messages, **kw):
        self.captured = messages
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


@pytest.mark.asyncio
async def test_judge_scoring_hints_injected_into_prompt():
    """非空 scoring_hints → 评分锚点（should_mention / should_not_hallucinate）注入 prompt。"""
    client = _CapturingLLM()
    judge = LLMJudge(client=client)
    hints = {
        "should_mention": ["余额增加", "记录交易"],
        "should_not_hallucinate": ["编造并发机制"],
    }
    await judge.judge("Q", "A", "ctx", [], rubric=QA_RUBRIC, scoring_hints=hints)
    user_msg = next(m["content"] for m in client.captured if m["role"] == "user")
    assert "=== 评分锚点 ===" in user_msg
    assert "余额增加" in user_msg and "记录交易" in user_msg
    assert "不应捏造" in user_msg and "编造并发机制" in user_msg


@pytest.mark.asyncio
async def test_judge_no_scoring_hints_omits_anchor_section():
    """无 scoring_hints（默认 None / 空）→ prompt 不含评分锚点段（向后兼容）。"""
    client = _CapturingLLM()
    judge = LLMJudge(client=client)
    await judge.judge("Q", "A", "ctx", [], rubric=QA_RUBRIC)  # scoring_hints 省略
    user_msg = next(m["content"] for m in client.captured if m["role"] == "user")
    assert "评分锚点" not in user_msg

    # 空子表（两列表都空）也应省略
    client2 = _CapturingLLM()
    await LLMJudge(client=client2).judge(
        "Q", "A", "ctx", [], rubric=QA_RUBRIC,
        scoring_hints={"should_mention": [], "should_not_hallucinate": []},
    )
    user_msg2 = next(m["content"] for m in client2.captured if m["role"] == "user")
    assert "评分锚点" not in user_msg2


@pytest.mark.asyncio
async def test_judge_diag_anchors_injected_into_prompt():
    """M40 诊断 expected 三元组 → 根因提示/相关代码/配置建议注入评分锚点段。"""
    client = _CapturingLLM()
    judge = LLMJudge(client=client)
    hints = {
        "root_cause_hints": ["消费者并发不足", "消费耗时过高"],
        "relevant_code": ["ConsumeMessageConcurrentlyService"],
        "config_suggestions": ["consumeThreadMin"],
    }
    await judge.judge("Q", "A", "ctx", [], rubric=QA_RUBRIC, scoring_hints=hints)
    user_msg = next(m["content"] for m in client.captured if m["role"] == "user")
    assert "=== 评分锚点 ===" in user_msg
    assert "根因提示" in user_msg and "消费者并发不足" in user_msg
    assert "相关代码" in user_msg and "ConsumeMessageConcurrentlyService" in user_msg
    assert "配置建议" in user_msg and "consumeThreadMin" in user_msg


@pytest.mark.asyncio
async def test_judge_diag_empty_lists_omit_anchor_section():
    """诊断三元组全为空列表 → 不渲染评分锚点段(向后兼容,d09/d10 无 root_cause_hints)。"""
    client = _CapturingLLM()
    judge = LLMJudge(client=client)
    hints = {"root_cause_hints": [], "relevant_code": [], "config_suggestions": []}
    await judge.judge("Q", "A", "ctx", [], rubric=QA_RUBRIC, scoring_hints=hints)
    user_msg = next(m["content"] for m in client.captured if m["role"] == "user")
    assert "评分锚点" not in user_msg
