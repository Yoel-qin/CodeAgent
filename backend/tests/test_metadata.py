"""metadata 纯函数单测（M31 起始：extract_chinese_comment）。零 infra。"""
from __future__ import annotations

from app.pipeline.metadata import enhance_code_chunk, extract_chinese_comment, short_hash
from app.pipeline.parsing.doc_element import CodeChunkSpec


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


# ---------- M32 ①a：enhance_code_chunk 规则注释增强 ----------


def _spec(chunk_type="method", content="public void send() {}", javadoc=None, cls="Cls",
          method="send", keywords=None, chunk_id=None):
    if chunk_id is None:
        chunk_id = f"code_{cls}_{method if method else 'x'}_{short_hash(content)}"
    return CodeChunkSpec(
        chunk_id=chunk_id, file_path="a/Cls.java", module_name="m", package_name="p",
        chunk_type=chunk_type, class_name=cls, method_name=method, method_signature=None,
        access_modifier=None, return_type=None, start_line=1, end_line=9,
        content=content, content_hash="x" * 64, javadoc=javadoc, inline_comments=[],
        annotations=[], implements_interface=None, extends_class=None, type_parameters=[],
        code_anchor_key=f"{cls}.{method}" if method else None,
        keywords=keywords if keywords is not None else ["send"], token_count=8,
        git_commit_hash="H", calls=[],
    )


def test_enhance_block_and_class_get_javadoc_prefix():
    jd = "/** 发送消息到 broker。 */"
    for ct in ("block", "class"):
        s = _spec(chunk_type=ct, javadoc=jd)
        out = enhance_code_chunk(s)
        assert out.content.startswith(jd + "\n")


def test_enhance_method_and_file_content_untouched():
    s = _spec(chunk_type="method", javadoc="/** doc */", content="javadoc 已在 source 内\npublic void send() {}")
    assert enhance_code_chunk(s).content == s.content
    f = _spec(chunk_type="file", cls=None, method=None, javadoc="/** doc */")
    assert enhance_code_chunk(f).content == f.content


def test_enhance_keywords_merge_chinese_from_javadoc():
    s = _spec(javadoc="/** 生产者发送消息的入口。 */", keywords=["send", "producer"])
    out = enhance_code_chunk(s)
    assert "生产者" in out.keywords      # jieba 中文词进入 keywords（PG 词法路缺口修复）
    assert "send" in out.keywords        # 原词保留


def test_enhance_keywords_cap_32():
    jd = "/** " + " ".join(f"词{i}组" for i in range(60)) + " */"
    s = _spec(javadoc=jd, keywords=[f"k{i}" for i in range(20)])
    assert len(enhance_code_chunk(s).keywords) <= 32


def test_enhance_chunk_id_tail_hash_replaced():
    old = "public void send() {}"
    s = _spec(chunk_type="block", content=old, javadoc="/** 注释 */",
              chunk_id=f"code_Cls_send_0_{short_hash(old)}")
    out = enhance_code_chunk(s)
    assert out.chunk_id.startswith("code_Cls_send_0_")
    assert out.chunk_id.endswith(short_hash(out.content))
    assert out.chunk_id != f"code_Cls_send_0_{short_hash(old)}"


def test_enhance_no_change_returns_spec_unchanged():
    s = _spec(javadoc=None, keywords=["send"])
    out = enhance_code_chunk(s)
    assert out is s and out.chunk_id == _spec(javadoc=None, keywords=["send"]).chunk_id


def test_chunk_code_file_enhance_switch_wiring(monkeypatch):
    """开关 on → chunk_code_file 应用增强；off（默认）→ 零变更。"""
    from app.core.config import settings
    from app.pipeline.chunking.code_chunker import chunk_code_file
    from app.pipeline.parsing.doc_element import CodeClass, CodeMethod, ParsedCodeFile

    jd = "/** 消费重试主题。 */"
    pf = ParsedCodeFile(
        file_path="a/Svc.java", package="p", imports=[], module_name="m", total_lines=500,
        classes=[CodeClass(
            name="Svc", kind="class", modifiers=["public"], annotations=[], javadoc=jd,
            superclass=None, interfaces=[], start_line=1, end_line=400,
            methods=[CodeMethod(
                name="retry", class_name="Svc", signature="public void retry()",
                modifiers=["public"], return_type=None, parameters=[], annotations=[],
                javadoc=jd, start_line=10, end_line=9,   # end<start → 超长触发块切分由 token 决定，此处不触发
                source=jd + "\npublic void retry() {}", calls=[],
            )],
            fields={},
        )],
        source="x" * 1000,
    )
    pf.total_lines = 500  # 走方法级切片

    monkeypatch.setattr(settings, "comment_enhance_enabled", False)
    off_specs = chunk_code_file(pf, commit_hash="H")
    monkeypatch.setattr(settings, "comment_enhance_enabled", True)
    on_specs = chunk_code_file(pf, commit_hash="H")
    # method chunk：content 已含 javadoc，仅 keywords 增（chunk_id 不变）；增强后 keywords 含中文
    assert any("重试" in (s.keywords or []) or "消费" in (s.keywords or []) for s in on_specs)
    assert not any("重试" in (s.keywords or []) for s in off_specs)
    assert {s.chunk_id for s in off_specs} == {s.chunk_id for s in on_specs}  # method content 未变 → id 未变
