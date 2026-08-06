"""Phase 1.5a 入库端到端单测（无基础设施）：_FakeSession + monkeypatch ES/Milvus，
验证 ingest_doc_file 把 PDF 解析 → upsert DocFile(file_format/total_pages/parse_engine)
→ 写 DocChunk(page_number) 的完整链路打通。
"""
from __future__ import annotations

from pathlib import Path

import fitz

import app.pipeline.indexing as indexing
from app.db.models import DocChunk, DocFile
from app.pipeline import ingest_doc


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """最小 session：add 记录对象；execute(select/delete/update) 一律返回空结果（走 insert 路径）。"""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, DocFile):
            obj.file_id = 1   # 模拟 flush 后的自增主键

    def execute(self, stmt):
        return _Result()

    def flush(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def get(self, *a, **k):
        return None


def _make_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Report Title", fontsize=24)
    page.insert_text((72, 120), "body line one.", fontsize=11)
    page.insert_text((72, 138), "body line two.", fontsize=11)
    doc.save(str(path))
    doc.close()


def test_ingest_doc_file_pdf_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing, "index_chunks_to_es", lambda *a, **k: None)
    monkeypatch.setattr(indexing, "index_chunks_to_milvus", lambda *a, **k: False)
    monkeypatch.setattr(indexing, "_embed_enabled_for", lambda *a, **k: False)

    p = tmp_path / "note.pdf"
    _make_pdf(p)
    session = _FakeSession()
    result = ingest_doc.ingest_doc_file(session, p, commit_hash="C1", repo_root=tmp_path)

    assert result["chunks"] > 0
    files = [o for o in session.added if isinstance(o, DocFile)]
    chunks = [o for o in session.added if isinstance(o, DocChunk)]
    assert len(files) == 1
    assert files[0].file_format == "pdf"
    assert files[0].parse_engine == "pymupdf"
    assert files[0].total_pages == 1
    assert files[0].parse_status == "COMPLETED"
    assert files[0].file_size_bytes and files[0].file_size_bytes > 0
    assert chunks, "应有 doc chunk"
    assert all(c.page_number == 1 for c in chunks)


def test_ingest_doc_file_failed_legacy_doc(tmp_path, monkeypatch):
    """legacy .doc → FAILED meta，仍记 DocFile 行但不产 chunk。"""
    monkeypatch.setattr(indexing, "index_chunks_to_es", lambda *a, **k: None)
    p = tmp_path / "old.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0")  # 伪 .doc 头
    session = _FakeSession()
    result = ingest_doc.ingest_doc_file(session, p, commit_hash="C1", repo_root=tmp_path)

    assert result["chunks"] == 0
    assert result["parse_status"] == "FAILED"
    files = [o for o in session.added if isinstance(o, DocFile)]
    assert len(files) == 1 and files[0].parse_status == "FAILED"
    assert files[0].file_format == "doc"


def test_ingest_markdown_source_backward_compat(tmp_path, monkeypatch):
    """内存 markdown 字符串入库仍可用（既有调用方契约），file_format=markdown。"""
    monkeypatch.setattr(indexing, "index_chunks_to_es", lambda *a, **k: None)
    monkeypatch.setattr(indexing, "index_chunks_to_milvus", lambda *a, **k: False)
    monkeypatch.setattr(indexing, "_embed_enabled_for", lambda *a, **k: False)
    session = _FakeSession()
    result = ingest_doc.ingest_markdown_source(
        session, source="# Title\n\n正文段落", file_path="t.md", commit_hash="C1")
    assert result["chunks"] >= 1
    files = [o for o in session.added if isinstance(o, DocFile)]
    assert files[0].file_format == "markdown"
