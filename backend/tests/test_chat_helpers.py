"""会话/消息 落库相关纯函数单元测试（无外部依赖）。"""
from __future__ import annotations

from app.core.ids import prefixed_id
from app.services.chat_service import _derive_title


def test_prefixed_id_format_and_uniqueness():
    a = prefixed_id("conv")
    b = prefixed_id("msg")
    assert a.startswith("conv_") and len(a) > len("conv_")
    assert b.startswith("msg_")
    assert a != prefixed_id("conv")  # 随机不可重复


def test_derive_title_truncates_long_query():
    long = "请详细解释事务消息的回查机制是如何实现的" * 5  # 远超 40 字
    title = _derive_title(long)
    assert title.endswith("…")
    assert len(title) == 41  # 40 + 省略号


def test_derive_title_short_query_kept_as_is():
    q = "checkLocalTransaction 是做什么的"
    assert _derive_title(q) == q


def test_derive_title_collapses_newlines():
    assert _derive_title("a\nb\nc") == "a b c"
