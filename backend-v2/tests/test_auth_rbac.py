"""Task 8：RBAC 地基。三个层级：
1. security 纯函数（哈希/JWT，离线）；
2. off 态匿名透传（auth_required=False + login 501 + 既有端点零 token 200）；
3. on 态（monkeypatch settings + 真 PG 种子用户真清理）：登录/401/403/禁用。

on 态种子走 sync engine 真插入真删除（login 经 SessionLocal 新连接，回滚 session
看不见未提交数据——同 test_chat_api 的清场模式）。"""
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_token, decode_token, hash_password, verify_password


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from app.agent import tools_loader
    from app.db.base import engine

    async def _noop_load(transports=None):
        return None

    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    yield
    slog = logging.getLogger("sqlalchemy")
    prev, slog.level = slog.level, logging.CRITICAL + 1
    try:
        import asyncio

        asyncio.run(engine.dispose())
    finally:
        slog.setLevel(prev)


# ── security 纯函数 ────────────────────────────────────────────────────────

def test_password_hash_roundtrip():
    h = hash_password("pw123456")
    assert h != "pw123456" and verify_password("pw123456", h)
    assert not verify_password("wrong", h)
    assert not verify_password("pw123456", "not-a-bcrypt-hash")


def test_token_roundtrip_and_expired(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret")
    token = create_token("alice")
    assert decode_token(token) == "alice"
    assert decode_token("garbage") is None
    assert decode_token("") is None
    monkeypatch.setattr(settings, "jwt_expire_minutes", -1)
    assert decode_token(create_token("bob")) is None  # 已过期


# ── off 态：匿名透传零行为变更 ──────────────────────────────────────────────

def test_off_anonymous_passthrough():
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/health").json()["auth_required"] is False
        assert client.post("/v1/auth/login",
                           json={"username": "x", "password": "y"}).status_code == 501
        assert client.post("/v1/auth/logout").status_code == 501
        assert client.get("/v1/repos").status_code == 200  # 零 token 直接过


# ── on 态：登录 + 门控（真 PG 种子） ────────────────────────────────────────

@pytest.fixture
def rbac_on(monkeypatch):
    monkeypatch.setattr(settings, "rbac_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret")
    yield
    # settings 是单例——monkeypatch fixture 结束自动还原，无需手工恢复


@pytest.fixture
def seeded_user():
    """种一个 developer 用户（密码 pw123456）+ 一个禁用用户；测后真删。"""
    from sqlalchemy import create_engine, text

    eng = create_engine(settings.postgres_dsn_sync)
    with eng.begin() as conn:
        rid = conn.execute(text("select id from roles where name='developer'")).scalar_one()
        conn.execute(text(
            "insert into users (username, password_hash, role_id, disabled) "
            "values (:u, :h, :r, false)"),
            {"u": "rbac-test-dev", "h": hash_password("pw123456"), "r": rid})
        conn.execute(text(
            "insert into users (username, password_hash, role_id, disabled) "
            "values (:u, :h, :r, true)"),
            {"u": "rbac-test-off", "h": hash_password("pw123456"), "r": rid})
    yield
    with eng.begin() as conn:
        conn.execute(text("delete from users where username like 'rbac-test-%'"))
    eng.dispose()


def _login(client: TestClient, username: str, password: str = "pw123456"):
    return client.post("/v1/auth/login",
                       json={"username": username, "password": password})


def test_on_login_and_gate(rbac_on, seeded_user):
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/health").json()["auth_required"] is True
        # 未带 token → 401
        assert client.get("/v1/repos").status_code == 401
        # 错密码 / 禁用 → 401
        assert _login(client, "rbac-test-dev", "wrong").status_code == 401
        assert _login(client, "rbac-test-off").status_code == 401
        # 正确登录 → token + user 载荷
        r = _login(client, "rbac-test-dev")
        assert r.status_code == 200
        body = r.json()
        assert body["token_type"] == "bearer" and body["user"]["role"] == "developer"
        token = body["access_token"]
        # 带 token → 200
        assert client.get("/v1/repos",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 200
        # 不存在的用户 token → 401（签发后又删的用户）
        ghost = create_token("rbac-test-ghost")
        assert client.get("/v1/repos",
                          headers={"Authorization": f"Bearer {ghost}"}).status_code == 401


def test_on_external_role_class_forbidden(rbac_on, seeded_user):
    """external 角色 endpoint_classes 无 graph → /v1/graph/search 403（repos 可见但类被拒）。"""
    from sqlalchemy import create_engine, text

    from app.main import app

    eng = create_engine(settings.postgres_dsn_sync)
    with eng.begin() as conn:
        rid = conn.execute(text("select id from roles where name='external'")).scalar_one()
        conn.execute(text(
            "insert into users (username, password_hash, role_id, disabled) "
            "values ('rbac-test-ext', :h, :r, false)"),
            {"h": hash_password("pw123456"), "r": rid})
    try:
        with TestClient(app) as client:
            token = _login(client, "rbac-test-ext").json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            assert client.get("/v1/repos", headers=headers).status_code == 200
            assert client.get("/v1/graph/search",
                              params={"q": "x", "repo": "rocketmq"},
                              headers=headers).status_code == 403
    finally:
        with eng.begin() as conn:
            conn.execute(text("delete from users where username='rbac-test-ext'"))
        eng.dispose()


def test_on_feedback_username(rbac_on, seeded_user):
    """KEEP②：RBAC on → feedback.username 落登录名（Bearer 认证身份）。"""
    from sqlalchemy import create_engine, text

    from app.main import app

    with TestClient(app) as client:
        token = _login(client, "rbac-test-dev").json()["access_token"]
        r = client.post("/v1/chat/messages/999997/feedback",
                        json={"rating": "NOT_HELPFUL"},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        fid = r.json()["feedback_id"]
    eng = create_engine(settings.postgres_dsn_sync)
    with eng.begin() as conn:
        u = conn.execute(text("select username from feedback where id=:i"),
                         {"i": fid}).scalar()
        conn.execute(text("delete from feedback where id=:i"), {"i": fid})
    eng.dispose()
    assert u == "rbac-test-dev"


# ── KEEP④：httpOnly cookie（Bearer 双读兼容） ──────────────────────────────


def test_login_sets_httponly_cookie(rbac_on, seeded_user):
    """login → Set-Cookie（HttpOnly）；cookie 单独可认证（无 Bearer header）。"""
    from app.main import app

    with TestClient(app) as client:
        r = _login(client, "rbac-test-dev")
        assert r.status_code == 200
        setc = r.headers.get("set-cookie", "")
        assert "coderag_token=" in setc and "HttpOnly" in setc and "Path=/" in setc
        token = r.json()["access_token"]
        # 显式 cookie、无 Authorization —— 证明 deps cookie 读路径生效
        r2 = client.get("/v1/repos", cookies={"coderag_token": token})
        assert r2.status_code == 200
        # Bearer 仍可认证（双读兼容，既有测试/调试姿势不变）
        r3 = client.get("/v1/repos", headers={"Authorization": f"Bearer {token}"})
        assert r3.status_code == 200


def test_logout_clears_cookie(rbac_on, seeded_user):
    """logout 无条件清 cookie（无认证依赖——清 cookie 永远成功）。"""
    from app.main import app

    with TestClient(app) as client:
        _login(client, "rbac-test-dev")
        r = client.post("/v1/auth/logout")
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert "coderag_token=" in r.headers.get("set-cookie", "")
