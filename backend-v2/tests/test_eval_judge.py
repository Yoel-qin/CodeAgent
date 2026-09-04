"""Task 5：LLMJudge——fake 模型注入（不触网）；坏输出/无 key 软失败为 None。"""
import json

from langchain_core.messages import AIMessage

from app.eval import judge


def _fake_model(text: str):
    class _M:
        def invoke(self, messages, config=None):
            return AIMessage(content=text)
    return _M()


async def test_judge_case_parses_plain_json(monkeypatch):
    monkeypatch.setattr(judge, "configured", lambda: True)
    monkeypatch.setattr(judge, "chat_model_for",
                        lambda _t="reasoning": _fake_model(
                            json.dumps({"faithfulness": 0.9, "answer_relevance": 0.8,
                                        "citation_accuracy": 0.7, "hallucination": 0.1})))
    scores = await judge.judge_case("q", "答案", [{"kind": "code", "file_path": "a.java",
                                                  "start_line": 1, "end_line": 1}])
    assert scores == {"faithfulness": 0.9, "answer_relevance": 0.8,
                      "citation_accuracy": 0.7, "hallucination": 0.1}


async def test_judge_case_tolerates_fence_and_clamps(monkeypatch):
    monkeypatch.setattr(judge, "configured", lambda: True)
    fenced = "```json\n{\"faithfulness\": 1.7, \"answer_relevance\": 0.5, " \
             "\"citation_accuracy\": 0.5, \"hallucination\": -0.2}\n```"
    monkeypatch.setattr(judge, "chat_model_for", lambda _t="reasoning": _fake_model(fenced))
    scores = await judge.judge_case("q", "a", [])
    assert scores["faithfulness"] == 1.0 and scores["hallucination"] == 0.0  # clamp 0..1


async def test_judge_case_soft_fails(monkeypatch):
    monkeypatch.setattr(judge, "configured", lambda: False)
    assert await judge.judge_case("q", "a", []) is None
    monkeypatch.setattr(judge, "configured", lambda: True)
    monkeypatch.setattr(judge, "chat_model_for",
                        lambda _t="reasoning": _fake_model("不是 JSON"))
    assert await judge.judge_case("q", "a", []) is None
    monkeypatch.setattr(judge, "chat_model_for",
                        lambda _t="reasoning": _fake_model(json.dumps({"faithfulness": 1.0})))
    assert await judge.judge_case("q", "a", []) is None  # 缺维 → None（不猜）


def test_judge_scores_macro_avg_and_none():
    rows = [{"faithfulness": 1.0, "answer_relevance": 0.0, "citation_accuracy": 0.0,
             "hallucination": 0.0}, None,
            {"faithfulness": 0.5, "answer_relevance": 1.0, "citation_accuracy": 0.0,
             "hallucination": 0.5}]
    out = judge.judge_scores(rows)
    assert out["faithfulness"] == 0.75 and out["answer_relevance"] == 0.5
    assert judge.judge_scores([None, None]) is None
