"""RBAC 鉴权依赖单测（零 infra）：off 透传 / on 401/403 / 归一化 / require_class / ensure_owner。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.deps import ANONYMOUS, CurrentUser


def _user(role="external", uid=2, kinds=None, classes=None):
    return CurrentUser(id=uid, username="u", role=role,
                       allowed_kinds=kinds, endpoint_classes=classes)


def test_anonymous_is_unrestricted():
    assert ANONYMOUS.allowed_kinds is None and ANONYMOUS.endpoint_classes is None
    assert ANONYMOUS.is_admin and ANONYMOUS.id is None


async def test_normalization_star_to_none(monkeypatch):
    """get_current_user 把 DB ["*"] 归一化为 None。"""
    from starlette.requests import Request

    import app.api.deps as deps
    from app.core.config import settings
    from app.services.auth_service import create_access_token

    monkeypatch.setattr(settings, "rbac_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "test-secret")
    token = create_access_token(5, "admin")

    class _S:
        async def get(self, cls, pk):
            assert pk == 5
            return SimpleNamespace(id=5, username="root", is_active=True,
                                   role=SimpleNamespace(name="admin",
                                                        allowed_kinds=["*"],
                                                        endpoint_classes=["*"]))

    req = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})
    user = await deps.get_current_user(req, _S())
    assert user.id == 5 and user.is_admin
    assert user.allowed_kinds is None and user.endpoint_classes is None


async def test_on_mode_no_token_401(monkeypatch):
    from starlette.requests import Request

    import app.api.deps as deps
    from app.core.config import settings

    monkeypatch.setattr(settings, "rbac_enabled", True)
    req = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as ei:
        await deps.get_current_user(req, None)
    assert ei.value.status_code == 401


async def test_inactive_user_403(monkeypatch):
    from starlette.requests import Request

    import app.api.deps as deps
    from app.core.config import settings
    from app.services.auth_service import create_access_token

    monkeypatch.setattr(settings, "rbac_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "test-secret")
    token = create_access_token(9, "ops")

    class _S:
        async def get(self, cls, pk):
            return SimpleNamespace(id=9, username="x", is_active=False,
                                   role=SimpleNamespace(name="ops",
                                                        allowed_kinds=["*"],
                                                        endpoint_classes=["chat"]))

    req = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})
    with pytest.raises(HTTPException) as ei:
        await deps.get_current_user(req, _S())
    assert ei.value.status_code == 403


async def test_require_class_pass_and_deny():
    import app.api.deps as deps

    ok = deps.require_class("chat")          # 依赖工厂 → 返回依赖函数（async）
    denied = deps.require_class("writeops")
    guest = _user(classes={"chat"})
    assert guest.is_admin is False
    assert await ok(guest) is None           # 通过 → None
    with pytest.raises(HTTPException) as ei:
        await denied(guest)
    assert ei.value.status_code == 403
    assert await ok(ANONYMOUS) is None       # 匿名（off）全通过


def test_ensure_owner():
    import app.api.deps as deps

    owner = _user(role="external", uid=2)
    other = _user(role="external", uid=3)
    admin = _user(role="admin", uid=99)

    deps.ensure_owner(owner, 2)          # 本人
    deps.ensure_owner(owner, None)       # 无属主（off 时期历史）
    deps.ensure_owner(admin, 2)          # admin 全见
    deps.ensure_owner(ANONYMOUS, 2)      # off 匿名
    with pytest.raises(HTTPException) as ei:
        deps.ensure_owner(other, 2)
    assert ei.value.status_code == 404


# ── 端到端路由级验证（TestClient + dependency_overrides）─────────────────────
async def test_search_endpoint_403_for_external(monkeypatch):
    from fastapi.testclient import TestClient

    import app.services.search_service as svc
    from app.api.deps import CurrentUser, get_current_user, get_db
    from app.main import app

    async def fake_recall(session, terms, *, top_k=20, allowed_kinds=None):
        return []

    monkeypatch.setattr(svc, "lexical_recall", fake_recall)

    external = CurrentUser(id=2, username="ext", role="external",
                           allowed_kinds={"doc", "table", "image"},
                           endpoint_classes={"chat", "search"})
    app.dependency_overrides[get_current_user] = lambda: external
    app.dependency_overrides[get_db] = lambda: None
    try:
        client = TestClient(app)
        assert client.get("/v1/search", params={"q": "x"}).status_code == 200   # search 类 ✓
        assert client.get("/v1/monitor/retrieval-perf").status_code == 403      # readops ✗
        assert client.get("/v1/sync/tasks").status_code == 403                  # writeops ✗（403 先于 404/路由逻辑）
    finally:
        app.dependency_overrides.clear()


async def test_health_reports_auth_required_off():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:   # 进 context 才跑 lifespan（fail-fast 不触发：默认 off）
        body = client.get("/health").json()
        assert body["auth_required"] is False
