"""带前缀的短 ID 生成（会话/消息等业务 ID）。"""
from __future__ import annotations

import secrets


def prefixed_id(prefix: str, *, nbytes: int = 8) -> str:
    """生成形如 ``conv_a1b2c3...`` 的不可猜测 ID。"""
    return f"{prefix}_{secrets.token_hex(nbytes)}"
