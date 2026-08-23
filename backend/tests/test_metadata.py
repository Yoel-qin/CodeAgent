"""metadata 纯函数单测（M31 起始：extract_chinese_comment）。零 infra。"""
from __future__ import annotations

from app.pipeline.metadata import extract_chinese_comment


def test_line_comment():
    src = "int a = 1; // 初始化计数器\nreturn a;"
    assert extract_chinese_comment(src) == "// 初始化计数器"


def test_block_comment_multiline_keeps_cjk_lines_only():
    src = "/* 第一行中文\n   second english line\n   第二行中文 */"
    out = extract_chinese_comment(src)
    assert "第一行中文" in out
    assert "第二行中文" in out
    assert "second english line" not in out


def test_javadoc():
    src = "/** 发送消息到 broker。\n * @param msg 消息体\n */\npublic void send() {}"
    out = extract_chinese_comment(src)
    assert "发送消息到 broker。" in out
    assert "@param msg 消息体" in out
    assert "public void send" not in out


def test_no_cjk_returns_empty():
    assert extract_chinese_comment("// english only\nint x; /* pure ascii */") == ""


def test_truncate_at_2000():
    src = "// " + "中" * 3000
    assert len(extract_chinese_comment(src)) == 2000


def test_empty_and_code_only():
    assert extract_chinese_comment("") == ""
    assert extract_chinese_comment("int x = f(y);") == ""
