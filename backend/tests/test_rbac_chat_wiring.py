"""M45 主链路接线测试（零 infra）：属主校验 helper + open_conversation 绑 user_id + recall 透传。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.deps import ANONYMOUS, CurrentUser


def _user(uid=2, role="external"):
    return CurrentUser(id=uid, username="u", role=role,
                       allowed_kinds={"doc"}, endpoint_classes={"chat"})


# ---- get_owned_conversation / get_owned_message_conversation ----


class _ConvSess:
    def __init__(self, conv):
        self._conv = conv

    async def get(self, cls, pk):
        return self._conv if pk == "conv_1" else None


async def test_get_owned_conversation():
    from app.services.chat_service import get_owned_conversation

    conv = SimpleNamespace(conversation_id="conv_1", user_id=2)
    sess = _ConvSess(conv)

    assert (await get_owned_conversation(sess, "conv_1", _user(uid=2))) is conv   # 本人
    assert (await get_owned_conversation(sess, "conv_1", ANONYMOUS)) is conv      # off 透传
    assert (await get_owned_conversation(sess, "conv_1", _user(uid=99, role="admin"))) is conv

    with pytest.raises(HTTPException) as ei:   # 他人 → 404（不暴露存在性）
        await get_owned_conversation(sess, "conv_1", _user(uid=3))
    assert ei.value.status_code == 404

    with pytest.raises(HTTPException) as ei2:  # 不存在 → 404
        await get_owned_conversation(sess, "conv_x", _user(uid=2))
    assert ei2.value.status_code == 404


# ---- open_conversation 绑 user_id ----


class _CreateSess:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = None

    async def get(self, cls, pk):
        return self.existing

    def add(self, obj):
        self.added = obj

    async def flush(self):
        return None


async def test_open_conversation_binds_user_id():
    from app.services.chat_service import open_conversation

    sess = _CreateSess()
    conv, _ = await open_conversation(sess, "首问", None, None, user_id=7)
    assert sess.added.user_id == 7                       # 新建绑定属主

    shared = SimpleNamespace(conversation_id="conv_9", user_id=None)   # off 时期历史
    sess2 = _CreateSess(existing=shared)
    conv2, _ = await open_conversation(sess2, "续问", None, "conv_9", user_id=7)
    assert conv2 is shared and shared.user_id is None    # 历史共享不被改写


# ---- stream_chat → recall 透传 allowed_kinds（legacy 路径，走桩）----


async def test_legacy_stream_passes_allowed_kinds(monkeypatch):
    import app.services.chat_service as cs

    captured: dict = {}

    class _Ranker:
        async def recall(self, session, query, **kw):
            captured.update(kw)
            return [{"chunk_id": "doc_a", "kind": "doc", "content": "c", "score": 1.0}], \
                   {"recall": {}, "merged": 1, "terms": []}

    monkeypatch.setattr(cs, "pipeline", _Ranker())
    async def _stub_enrich(s, r):
        return None
    monkeypatch.setattr(cs, "_enrich_content_types", _stub_enrich)
    async def _stub_persist(*a, **k):
        return SimpleNamespace(log_id=1)
    monkeypatch.setattr(cs, "persist_retrieval_log", _stub_persist)
    monkeypatch.setattr(cs, "load_conversation_history",
                        lambda *a, **k: [])
    monkeypatch.setattr(cs, "llm", SimpleNamespace(configured=False))  # 无 LLM → 生成跳过
    async def _stub_cache(*a, **k):
        return None
    monkeypatch.setattr(cs, "_qa_cache_lookup", _stub_cache)
    monkeypatch.setattr(cs, "make_cost_controller", lambda: None)
    async def _stub_open(s, q, at, cid, target_repo=None, user_id=None):
        return (SimpleNamespace(conversation_id="c1", title="t", agent_type=None,
                                message_count=0, target_repo=None), "c1")
    monkeypatch.setattr(cs, "open_conversation", _stub_open)
    async def _stub_user_msg(*a, **k):
        return "m1"
    async def _stub_assistant(*a, **k):
        return "m2"
    monkeypatch.setattr(cs, "add_user_message", _stub_user_msg)
    monkeypatch.setattr(cs, "add_assistant_message", _stub_assistant)

    events = []
    async for ev in cs.stream_chat(None, "q", user=_user()):
        events.append(ev)
    assert captured.get("allowed_kinds") == {"doc"}
    assert [e for e, _ in events][0] == "conversation"   # SSE 契约不变
