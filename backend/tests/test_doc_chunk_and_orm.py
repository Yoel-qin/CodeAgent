"""Phase 1.5a 切片透传 + ORM 落库单测：parse → chunk_doc_elements → _to_orm，
验证 page_number（PDF）与 chunk_content_type（text/code_block）正确落 DocChunk。无 DB。"""
from __future__ import annotations

from pathlib import Path

import fitz

from app.pipeline.chunking.doc_chunker import chunk_doc_elements
from app.pipeline.ingest_doc import _to_orm
from app.pipeline.parsing.doc_element import DocElement
from app.pipeline.parsing.pdf_parser import parse_pdf


def _make_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Section Heading", fontsize=22)
    page.insert_text((72, 110), "body line one", fontsize=11)
    page.insert_text((72, 128), "body line two", fontsize=11)
    doc.save(str(path))
    doc.close()


def test_pdf_page_number_propagates_to_chunk(tmp_path):
    p = tmp_path / "x.pdf"
    _make_pdf(p)
    elements, _ = parse_pdf(p.read_bytes(), "x.pdf")
    specs = chunk_doc_elements(elements, file_path="x.pdf", file_hash="abcdef0123456789",
                              commit_hash="C")
    orm = [_to_orm(s, file_id=1) for s in specs]
    assert orm, "应至少一个 chunk"
    assert all(c.page_number == 1 for c in orm), "PDF chunk 应透传 page_number=1"
    assert all(c.chunk_content_type == "text" for c in orm)


def test_code_block_content_type():
    code = "\n".join(["```java"] + [f"int x{i} = {i};" for i in range(6)] + ["```"])
    elements = [
        DocElement(type="HEADING", content="Section", heading_level=2),
        DocElement(type="CODE_BLOCK", content=code),
    ]
    specs = chunk_doc_elements(elements, file_path="t.md", file_hash="h" * 16, commit_hash="C")
    code_specs = [s for s in specs if s.chunk_content_type == "code_block"]
    assert code_specs, "≥5 行代码块应独立成 chunk 且标 code_block"
    assert _to_orm(code_specs[0], 1).chunk_content_type == "code_block"


def test_markdown_chunk_content_type_defaults_text():
    elements = [
        DocElement(type="HEADING", content="Title", heading_level=1),
        DocElement(type="PARAGRAPH", content="plain prose"),
    ]
    specs = chunk_doc_elements(elements, file_path="t.md", file_hash="h" * 16, commit_hash="C")
    assert specs and all(s.chunk_content_type == "text" for s in specs)
    assert all(s.page_number is None for s in specs)
