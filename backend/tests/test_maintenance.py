"""运营加固单测（Phase 7 Milestone 14 Part B）：HITL 超时过期 + 历史过滤 + /resume 409。

假 session（按 SQL 前缀分发 / 捕获 ORM select），无需 infra。
"""
from __future__ import annotations

from types import SimpleNamespace

import app.services.maintenance_service as ms
from app.services.chat_service import load_conversation_history

# ---- 通用假结果 / session ----


class _Result:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def all(self):
        return list(self._rows)


class _ExpireSession:
    """``expire_stale_interrupts`` 用：SELECT 走 .all()、UPDATE 走 rowcount，记 execute + commit。"""

    def __init__(self, rows):
        self._rows = rows  # [(msg_id, cid), ...]
        self.executes: list[tuple] = []
        self.commits = 0

    async def execute(self, stmt, params=None):
        self.executes.append((stmt, params))
        if str(stmt).strip().upper().startswith("SELECT"):
            return _Result(rows=self._rows)
        return _Result(rowcount=1)

    async def commit(self):
        self.commits += 1


class _CaptureSession:
    """``load_conversation_history`` 用：捕获 ORM select stmt（断言 status 过滤）。"""

    def __init__(self):
        self.stmt = None

    async def execute(self, stmt, params=None):
        self.stmt = stmt
        return _Result(rows=[])


# ---- expire_stale_interrupts ----


async def test_expire_flips_interrupted_to_expired_and_returns_cids():
    session = _ExpireSession([("msg_1", "conv_a"), ("msg_2", "conv_a"), ("msg_3", "conv_b")])
    cids = await ms.expire_stale_interrupts(session, timeout_hours=24)
    assert cids == ["conv_a", "conv_b"]  # 去重保序
    assert len(session.executes) == 4  # 1 SELECT + 3 UPDATE
    assert session.commits == 1
    upd = [s for s, _ in session.executes if str(s).strip().upper().startswith("UPDATE")]
    assert len(upd) == 3
    for s, p in session.executes:
        if str(s).strip().upper().startswith("UPDATE"):
            assert "status = 'expired'" in str(s).lower().replace("  ", " ")


async def test_expire_empty_still_commits_returns_empty():
    session = _ExpireSession([])
    cids = await ms.expire_stale_interrupts(session, timeout_hours=24)
    assert cids == []
    assert len(session.executes) == 1  # 仅 SELECT
    assert session.commits == 1  # 幂等 commit


async def test_expire_timeout_le_zero_disabled():
    session = _ExpireSession([("msg_1", "conv_a")])
    assert await ms.expire_stale_interrupts(session, timeout_hours=0) == []
    assert session.executes == [] and session.commits == 0  # 直接短路


# ---- load_conversation_history：过滤 interrupted/expired ----


async def test_history_query_excludes_interrupted_and_expired():
    session = _CaptureSession()
    await load_conversation_history(session, "conv_1", exclude_message_id="msg_cur", limit=6)
    assert session.stmt is not None
    sql = str(session.stmt.compile(compile_kwargs={"literal_binds": True})).upper()
    assert "NOT IN" in sql and "INTERRUPTED" in sql and "EXPIRED" in sql


# ---- /resume 409：非 interrupted 态拒绝 ----


async def test_resume_rejects_non_interrupted_status(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.deps import get_db
    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "rag_engine", "langgraph")

    class _StatusSession:
        def __init__(self, status):
            self._status = status

        async def get(self, model, pk):  # get_message_status 仅用此
            return SimpleNamespace(status=self._status) if self._status else None

    async def _override():
        return _StatusSession("expired")  # 已过期

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/resume",
            json={"conversation_id": "conv_1", "message_id": "msg_x", "approved": True},
        )
        assert resp.status_code == 409
        assert "不在待审批状态" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db, None)
