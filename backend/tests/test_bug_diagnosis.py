"""缺陷诊断 Agent（create_react_agent）集成测：自动工具调用循环 + 事件桥接 + 兜底降级。

与 test_change_impact.py / test_doc_answer.py / test_code_understand.py 同构（共享骨架
run_scenario_agent）：用一个可流的假 ChatModel（先发 search_code tool_call、再发最终答案，
作答轮经 run_manager.on_llm_new_token 推 token），mock 检索层。验证：
  - retrieval（agent=BUG_DIAGNOSIS）→ agent_step（{tool,args,n}）+ citation → token 的事件序；
  - get_bug_diagnosis_agent 异常 → _degrade 兜底仍产出 token。
"""
from __future__ import annotations

import warnings

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import END, START, StateGraph

import app.agent.agents._base as base
import app.agent.agents.bug_diagnosis as bd
import app.clients.llm_client as llm_mod
from app.agent.agents.bug_diagnosis import bug_diagnosis
from app.agent.state import AgentState

# 抑制 create_react_agent 弃用告警（langgraph-prebuilt v1 标记，功能仍完整）
warnings.filterwarnings("ignore")


class StepModel(BaseChatModel):
    """假模型：第 1 轮发 search_code 工具调用，第 2 轮发最终答案（经 run_manager 推 token）。"""

    step: int = 0

    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kw):
        return self

    def with_structured_output(self, schema, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        self.step += 1
        if self.step == 1:
            msg = AIMessage(content="", tool_calls=[
                {"name": "search_code", "args": {"query": "x"}, "id": "tc1", "type": "tool_call"}])
        else:
            if run_manager is not None:
                for t in ("FI", "NAL"):
                    run_manager.on_llm_new_token(t)
            msg = AIMessage(content="FINAL")
        return ChatResult(generations=[ChatGeneration(message=msg)])


async def _run_bug(state: dict, config: dict) -> list[dict]:
    g = StateGraph(AgentState)
    g.add_node("bug", bug_diagnosis)
    g.add_edge(START, "bug")
    g.add_edge("bug", END)
    events: list[dict] = []
    async for chunk in g.compile().astream(state, config=config, stream_mode="custom"):
        events.append(chunk)
    return events


# ---- 自动 Agent 循环：工具调用 → 引用/步骤事件 → 最终答案 token ----


async def test_bug_diagnosis_loop_bridges_events(monkeypatch):
    monkeypatch.setattr(base, "configured", lambda: True)
    monkeypatch.setattr(bd, "get_chat_model", lambda: StepModel())
    monkeypatch.setattr(bd, "_agent", None)  # 强制用假模型重建 Agent

    async def fake_recall(session, query, **kw):
        return ([{"chunk_id": "c1", "kind": "code", "content": "src", "class_name": "A",
                  "method_name": "m", "score": 0.9}], {"recall": {}, "fine": 1})

    monkeypatch.setattr("app.retrieval.pipeline.pipeline.recall", fake_recall)

    events = await _run_bug(
        {"query": "为什么 A.m 会报错", "keywords": ["A", "m"], "rewritten": False},
        {"configurable": {"thread_id": "t", "session": object(), "top_k": 8,
                          "agent_type": "BUG_DIAGNOSIS"}},
    )
    seq = [e["event"] for e in events]
    assert seq[0] == "retrieval"                          # _emit_retrieval_meta 前置
    assert events[0]["data"]["agent"] == "BUG_DIAGNOSIS"  # 缺陷诊断 Agent 标签
    assert "agent_step" in seq                            # search_code 执行
    # agent_step 事件 shape：{tool, args, n}（M5 可观测性持久化依赖此结构）
    step_evt = next(e for e in events if e["event"] == "agent_step")
    assert step_evt["data"]["tool"] == "search_code"
    assert step_evt["data"]["args"] == {"query": "x"}
    assert isinstance(step_evt["data"]["n"], int) and step_evt["data"]["n"] >= 1
    assert "citation" in seq                              # c1 被引用
    cit_idx = [i for i, e in enumerate(events) if e["event"] == "citation"]
    tok_idx = [i for i, e in enumerate(events) if e["event"] == "token"]
    assert tok_idx and max(cit_idx) < min(tok_idx)        # 引用在 token 之前
    assert "".join(e["data"]["content"] for e in events if e["event"] == "token") == "FINAL"


# ---- 兜底降级：Agent 构建失败 → _degrade 仍作答 ----


async def test_bug_diagnosis_degrades_on_failure(monkeypatch):
    monkeypatch.setattr(base, "configured", lambda: True)

    def boom():
        raise RuntimeError("agent build failed")

    monkeypatch.setattr(bd, "get_bug_diagnosis_agent", boom)

    async def fake_recall(session, query, **kw):
        return ([{"chunk_id": "c1", "kind": "code", "content": "src", "class_name": "A",
                  "method_name": "m", "score": 0.9}],
                {"recall": {}, "fine": 1, "merged": 1, "terms": ["x"]})

    monkeypatch.setattr("app.retrieval.pipeline.pipeline.recall", fake_recall)

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(base, "_enrich_content_types", noop)
    monkeypatch.setattr(llm_mod.LLMClient, "configured", property(lambda self: True))

    async def fake_stream(messages):
        for t in ("DE", "GRADE"):
            yield t

    monkeypatch.setattr(llm_mod.llm, "stream_tokens", fake_stream)

    events = await _run_bug(
        {"query": "x", "keywords": ["x"], "rewritten": False},
        {"configurable": {"thread_id": "t", "session": object(), "top_k": 8, "agent_type": None}},
    )
    seq = [e["event"] for e in events]
    assert "retrieval" in seq and "citation" in seq and "token" in seq  # 兜底仍产出完整事件
    assert "".join(e["data"]["content"] for e in events if e["event"] == "token") == "DEGRADE"
