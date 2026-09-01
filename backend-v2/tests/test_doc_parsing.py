import fitz  # pymupdf
from docx import Document as DocxDocument

from app.pipeline.parsing import parse_doc


def test_markdown_headings_and_paragraph():
    md = "# 标题一\n\n段落甲。\n\n## 小节\n\n段落乙。\n".encode()
    elems, meta = parse_doc(md, ".md", "guide.md")
    assert meta.file_format == "markdown" and meta.parse_status == "COMPLETED"
    types = [e.type for e in elems]
    assert "HEADING" in types and "PARAGRAPH" in types
    para = next(e for e in elems if e.type == "PARAGRAPH" and "段落甲" in e.content)
    assert para.heading_path == ["标题一"]


def test_txt_plain():
    elems, meta = parse_doc(b"hello\nworld", ".txt", "a.txt")
    assert meta.parse_engine == "text" and elems


def test_pdf_generated_in_test(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PDF section text line")
    data = doc.tobytes()
    elems, meta = parse_doc(data, ".pdf", "a.pdf")
    assert meta.file_format == "pdf" and meta.total_pages == 1
    assert any("PDF section text" in e.content for e in elems if e.type == "PARAGRAPH")


def test_docx_generated_in_test(tmp_path):
    d = DocxDocument()
    d.add_heading("Doc Title", level=1)
    d.add_paragraph("Body text here.")
    p = tmp_path / "a.docx"
    d.save(str(p))
    elems, meta = parse_doc(p.read_bytes(), ".docx", "a.docx")
    assert meta.file_format == "docx"
    assert any("Doc Title" in (e.content or "") for e in elems if e.type == "HEADING")
