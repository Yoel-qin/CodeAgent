"""跨轮对话记忆（Phase 7 Milestone 9）单测。

覆盖三类：
  - ``chat_service.load_conversation_history``：limit<=0 短路 / 查询构造（WHERE 排除当前轮 +
    ORDER DESC + LIMIT）/ DESC→升序反转 / 长内容截断 / 形状与角色保留。
  - ``chat_service.build_messages``：history 插在 system 与当前 user 间；None/[] 与无历史逐字一致。
  - 接线：``generate`` 节点把 history 传入 build_messages；``_base.run_scenario_agent`` 把
    history 前置进 Agent 消息种子。

SQL 的实际执行语义（排除当前轮 / DESC / LIMIT N 落到真表）由 e2e 覆盖；此处用 mock session
验证「查询构造 + 后处理（反转/截断/形状）」。
"""
from __future__ import annotations

import app.agent.agents._base as base
import app.clients.llm_client as llm_mod
from app.agent.nodes.generate import generate
from app.services.chat_service import build_messages, load_conversation_history

# ---- fake session：记录最后一条 stmt，返回预置 rows（list[(role, content)]）----


class _FakeResult:
    def __init__(self, rows): self._rows = rows

    def all(self): return self._rows


class _FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.last_stmt = None
        self.execute_calls = 0

    async def execute(self, stmt):
        self.last_stmt = stmt
        self.execute_calls += 1
        return _FakeResult(self.rows)


# ---- load_conversation_history ----


async def test_history_disabled_returns_empty_without_query():
    """limit<=0 → []（禁用），且不触 DB（短路）。"""
    session = _FakeSession([("user", "q1")])
    out = await load_conversation_history(
        session, "conv_1", exclude_message_id="msg_cur", limit=0,
    )
    assert out == []
    assert session.execute_calls == 0  # 短路，未查库


async def test_history_query_built_correctly():
    """断言编译后的 SQL 含 conversation_id、排除当前轮 id、DESC、LIMIT。"""
    session = _FakeSession([])
    await load_conversation_history(
        session, "conv_1", exclude_message_id="msg_cur", limit=6,
    )
    sql = str(session.last_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "conv_1" in sql                       # WHERE conversation_id
    assert "msg_cur" in sql                      # WHERE message_id != 当前轮
    assert "DESC" in sql                         # ORDER BY created_at DESC
    assert "LIMIT" in sql                        # LIMIT 6


async def test_history_desc_rows_returned_ascending():
    """DB 以 DESC 返回最近 N 条 → 函数反转为升序（最旧→最新），LLM 时序正确。"""
    # 模拟 DB ORDER BY created_at DESC 的返回（最新在前）
    session = _FakeSession([("assistant", "a2"), ("user", "q2"),
                            ("assistant", "a1"), ("user", "q1")])
    out = await load_conversation_history(
        session, "conv_1", exclude_message_id="msg_cur", limit=6,
    )
    assert [m["content"] for m in out] == ["q1", "a1", "q2", "a2"]  # 升序


async def test_history_truncates_long_content_with_marker():
    """超 _HISTORY_MSG_MAX_CHARS 的内容截断 + 标记。"""
    from app.services.chat_service import _HISTORY_MSG_MAX_CHARS
    long = "x" * (_HISTORY_MSG_MAX_CHARS + 500)
    session = _FakeSession([("assistant", long)])
    out = await load_conversation_history(
        session, "conv_1", exclude_message_id="msg_cur", limit=6,
    )
    assert out[0]["content"].endswith("…[已截断]")
    assert len(out[0]["content"]) == _HISTORY_MSG_MAX_CHARS + len("…[已截断]")


async def test_history_empty_rows_returns_empty():
    """无先前消息 → []。"""
    session = _FakeSession([])
    out = await load_conversation_history(
        session, "conv_1", exclude_message_id="msg_cur", limit=6,
    )
    assert out == []


async def test_history_preserves_role_and_shape():
    """每项为 {role, content}，user/assistant 角色保留。"""
    session = _FakeSession([("assistant", "a1"), ("user", "q1")])
    out = await load_conversation_history(
        session, "conv_1", exclude_message_id="msg_cur", limit=6,
    )
    assert out == [{"role": "user", "content": "q1"},
                   {"role": "assistant", "content": "a1"}]


# ---- build_messages ----


def test_build_messages_with_history_inserts_between_system_and_user():
    msgs = build_messages("now", "ctx", "CODE_UNDERSTAND",
                          history=[{"role": "user", "content": "q1"},
                                   {"role": "assistant", "content": "a1"}])
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "q1"}
    assert msgs[2] == {"role": "assistant", "content": "a1"}
    assert msgs[3]["role"] == "user" and "now" in msgs[3]["content"]


def test_build_messages_no_history_is_byte_identical_to_legacy():
    """None / [] 与无历史逐字一致（回归保障）。"""
    base_msgs = build_messages("q", "ctx", None)
    none_msgs = build_messages("q", "ctx", None, history=None)
    empty_msgs = build_messages("q", "ctx", None, history=[])
    assert none_msgs == base_msgs == empty_msgs
    assert [m["role"] for m in base_msgs] == ["system", "user"]


# ---- generate 节点接线 ----


async def test_generate_passes_history_to_build_messages(monkeypatch):
    """generate 把 state.history 透传给 build_messages（历史出现在喂给 LLM 的消息里）。"""
    pushed: list[dict] = []
    monkeypatch.setattr("app.agent.nodes.generate.get_stream_writer",
                        lambda: lambda d: pushed.append(d))
    monkeypatch.setattr(llm_mod.LLMClient, "configured", property(lambda self: True))

    seen: list[list[dict]] = []

    async def fake_stream(messages):
        seen.append(messages)
        yield "ok"

    monkeypatch.setattr(llm_mod.llm, "stream_tokens", fake_stream)

    await generate(
        {"query": "now", "ranked": [], "retrieval_meta": {},
         "history": [{"role": "user", "content": "q1"},
                     {"role": "assistant", "content": "a1"}]},
        {"configurable": {"agent_type": None}},
    )
    msgs = seen[0]
    roles = [m["role"] for m in msgs]
    assert roles[0] == "system"
    assert {"role": "assistant", "content": "a1"} in msgs  # 历史被带入
    assert roles[-1] == "user"  # 当前轮在末尾


# ---- _base.run_scenario_agent 接线 ----


class _FakeAgent:
    """记录 astream 收到的 payload（{messages: seed}）；产出空流。"""

    def __init__(self): self.seeds: list[dict] = []

    async def astream(self, payload, **kw):
        self.seeds.append(payload)
        return
        yield  # 使其成为 async generator


async def test_base_seeds_history_then_current_query(monkeypatch):
    """run_scenario_agent 把 [*history, 当前 query] 作为 Agent 消息种子。"""
    monkeypatch.setattr(base, "configured", lambda: True)
    fake = _FakeAgent()
    state = {"query": "now",
             "history": [{"role": "user", "content": "q1"},
                         {"role": "assistant", "content": "a1"}]}
    config = {"configurable": {"session": object(), "top_k": 8, "agent_type": "X"}}
    await base.run_scenario_agent(
        state, config, agent_name="X", tools=[], build_agent=lambda: fake,
        degrade_label="X",
    )
    assert fake.seeds, "Agent.astream 应被调用一次"
    assert fake.seeds[0]["messages"] == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "now"},
    ]
