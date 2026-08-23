"""共享 ES doc 构建函数（M31）：ingest 与 rebuild 同源；on 附 chinese_comment / off 不附。"""
from __future__ import annotations

from types import SimpleNamespace

from app.core.config import settings
from app.pipeline import indexing


def _code_spec():
    return SimpleNamespace(
        chunk_id="code_abc12345", content="/** 发送消息 */\npublic void send() {}",
        keywords=["send"], class_name="DefaultMQProducerImpl", method_name="send",
    )


def _doc_spec():
    return SimpleNamespace(
        chunk_id="doc_def67890", content="# 使用指南\n发送消息",
        keywords=["指南"], heading_path=["使用指南"],
    )


def test_build_code_es_doc_off_has_no_chinese_comment(monkeypatch):
    monkeypatch.setattr(settings, "es_ik_enabled", False)
    doc = indexing.build_code_es_doc(_code_spec(), "src/A.java")
    assert doc == {
        "chunk_id": "code_abc12345", "kind": "code",
        "content": "/** 发送消息 */\npublic void send() {}",
        "keywords": ["send"], "class_name": "DefaultMQProducerImpl",
        "method_name": "send", "heading_path": [], "file_path": "src/A.java",
    }
    assert "chinese_comment" not in doc   # off 不写键——防旧索引 dynamic mapping 自动建字段


def test_build_code_es_doc_on_appends_chinese_comment(monkeypatch):
    monkeypatch.setattr(settings, "es_ik_enabled", True)
    doc = indexing.build_code_es_doc(_code_spec(), "src/A.java")
    assert doc["chinese_comment"] == "/** 发送消息 */"   # content 内含 CJK 的注释行
    assert doc["kind"] == "code"


def test_build_doc_es_doc_never_appends_chinese_comment(monkeypatch):
    """doc chunk 不加 chinese_comment——其 content 本身即中文，IK 直接受益（spec §3.5）。"""
    for flag in (False, True):
        monkeypatch.setattr(settings, "es_ik_enabled", flag)
        doc = indexing.build_doc_es_doc(_doc_spec(), "docs/guide.md")
        assert doc == {
            "chunk_id": "doc_def67890", "kind": "doc",
            "content": "# 使用指南\n发送消息", "keywords": ["指南"],
            "class_name": None, "method_name": None,
            "heading_path": ["使用指南"], "file_path": "docs/guide.md",
        }
        assert "chinese_comment" not in doc
