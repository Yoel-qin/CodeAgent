"""M43 反馈端点单测（TestClient + 假 session，无 DB）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app


class _FakeSession:
    def __init__(self, msg):
        self._msg = msg
        self.committed = 0

    async def get(self, model, pk):
        return self._msg if model.__name__ == "ChatMessage" else None

    async def execute(self, stmt):
        class _R:
            def scalars(self):
                return self
            def first(self):
                return None
        return _R()

    def add(self, obj):
        pass

    async def commit(self):
        self.committed += 1


@pytest.fixture
def client():
    # 假 session 经 dependency_overrides 注入；message 查得到（retrieval_log_id=None → persisted=False）
    msg = type("M", (), {"message_id": "m1", "conversation_id": "c1",
                         "retrieval_log_id": None})()
    app.dependency_overrides[get_db] = lambda: _FakeSession(msg)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_feedback_legacy_body_unchanged(client):
    r = client.post("/v1/chat/messages/m1/feedback", json={"rating": "HELPFUL"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["feedback"] == "HELPFUL" and body["persisted"] is False
    assert body.get("candidate_created") is False          # 新键默认 False


def test_feedback_helpful_with_categories_422(client):
    r = client.post("/v1/chat/messages/m1/feedback",
                    json={"rating": "HELPFUL", "categories": ["答案错误"]})
    assert r.status_code == 422


def test_feedback_unknown_category_422(client):
    r = client.post("/v1/chat/messages/m1/feedback",
                    json={"rating": "NOT_HELPFUL", "categories": ["不存在的分类"]})
    assert r.status_code == 422


def test_feedback_correction_too_long_422(client):
    r = client.post("/v1/chat/messages/m1/feedback",
                    json={"rating": "NOT_HELPFUL", "correction": "x" * 2001})
    assert r.status_code == 422


def test_feedback_message_not_found_404(client):
    sess = _FakeSession(None)
    app.dependency_overrides[get_db] = lambda: sess
    r = client.post("/v1/chat/messages/missing/feedback", json={"rating": "HELPFUL"})
    assert r.status_code == 404
