"""M45 ⌘K 过滤 + create_user CLI 测试（零 infra）。"""
from __future__ import annotations

from types import SimpleNamespace


async def test_search_service_filters_by_allowed_kinds(monkeypatch):
    import app.services.search_service as svc

    captured: dict = {}

    async def fake_recall(session, terms, *, top_k=20, allowed_kinds=None):
        captured["allowed_kinds"] = allowed_kinds
        return [
            {"chunk_id": "doc_1", "kind": "doc", "content": "存款流程",
             "heading_path": ["事务"], "score": 2.0},
        ]

    monkeypatch.setattr(svc, "lexical_recall", fake_recall)
    data = await svc.search(None, "存款", allowed_kinds={"doc"})
    assert captured["allowed_kinds"] == {"doc"} and data["total"] == 1


async def test_create_user_helper(monkeypatch):
    from scripts.create_user import create_user

    added = {}

    class _Q:
        def scalars(self):
            return self

        def first(self):
            return None

    class _RoleQ:
        def __init__(self, role):
            self._role = role

        def scalars(self):
            return self

        def first(self):
            return SimpleNamespace(id=1, name=self._role)

    class _Sess:
        def execute(self, stmt):
            s = str(stmt)
            if "roles" in s and "users" not in s:
                return _RoleQ("external")
            return _Q()

        def add(self, obj):
            added["user"] = obj

        def commit(self):
            added["committed"] = True

    # create_user(session, username, password, role_name) -> User（同步引擎签名见实现）
    _ = create_user(_Sess(), "alice", "pw123456", "external")
    assert added["user"].username == "alice"
    assert added["user"].password_hash.startswith("$2")
    assert added["user"].role_id == 1 and added["user"].is_active
    assert added["committed"]
