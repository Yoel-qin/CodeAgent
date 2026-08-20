"""auth_service 单测（零 infra）：bcrypt 往返 + JWT 签发/校验/过期 + authenticate 走桩。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_password_hash_roundtrip():
    from app.services.auth_service import hash_password, verify_password

    h = hash_password("s3cret!")
    assert h != "s3cret!" and h.startswith("$2")      # bcrypt 格式
    assert verify_password("s3cret!", h)
    assert not verify_password("wrong", h)


def test_token_roundtrip_and_expiry(monkeypatch):
    from app.services import auth_service as svc

    tok = svc.create_access_token(7, "developer", expires_minutes=5)
    payload = svc.decode_token(tok)
    assert payload["sub"] == "7" and payload["role"] == "developer" and "exp" in payload

    expired = svc.create_access_token(7, "developer", expires_minutes=-1)
    with pytest.raises(ValueError):
        svc.decode_token(expired)

    with pytest.raises(ValueError):
        svc.decode_token("not-a-jwt")


async def test_authenticate_ok_and_bad(monkeypatch):
    from app.services import auth_service as svc

    fake_user = SimpleNamespace(
        id=1, username="u", is_active=True,
        password_hash=svc.hash_password("pw"),
        role=SimpleNamespace(name="developer", allowed_kinds=["*"], endpoint_classes=["*"]),
    )

    class _S:
        async def execute(self, *a, **k):
            class _R:
                def scalars(self_inner):
                    return self_inner
                def first(self_inner):
                    return fake_user
            return _R()

    assert await svc.authenticate(_S(), "u", "pw") is fake_user
    assert await svc.authenticate(_S(), "u", "bad") is None
