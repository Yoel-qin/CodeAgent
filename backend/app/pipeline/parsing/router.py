"""多格式文档解析路由（Phase 1.5a 真相源）。

- ``DOC_FORMAT_EXTS`` / ``EXT_KIND``：ext→file_format / ext→code|doc。
  ``app/pipeline/ingest.py`` 的 ``DEFAULT_EXTS`` 与 ``app/pipeline/sync_git.py`` 的
  ``_EXT_KIND`` 都引用 ``EXT_KIND``——格式映射唯一真相源，二者永不错位。
- :func:`parse_doc`：按扩展名分发到 markdown/pdf/docx/txt 解析器，返回
  ``(list[DocElement], ParseMeta)``。
- :func:`decode_text`：从 :mod:`txt_parser` 复用的 UTF-8→gb18030→latin-1 解码阶梯
  （markdown/html 路由用）。
"""
from __future__ import annotations

from app.pipeline.parsing.doc_element import DocElement, ParseMeta
from app.pipeline.parsing.markdown_parser import parse_markdown
from app.pipeline.parsing.pdf_parser import parse_pdf
from app.pipeline.parsing.txt_parser import decode_text, parse_txt
from app.pipeline.parsing.word_parser import parse_docx

# ext → doc_files.file_format
DOC_FORMAT_EXTS: dict[str, str] = {
    ".md": "markdown", ".markdown": "markdown", ".html": "html",
    ".pdf": "pdf",
    ".docx": "docx", ".doc": "doc",
    ".txt": "txt",
}
# ext → code|doc（ingest.py DEFAULT_EXTS / sync_git._EXT_KIND 共用）
EXT_KIND: dict[str, str] = {".java": "code", **{ext: "doc" for ext in DOC_FORMAT_EXTS}}


def doc_format_for(ext: str) -> str | None:
    """扩展名 → file_format（未知返回 None）。"""
    return DOC_FORMAT_EXTS.get(ext.lower())


def parse_doc(data: bytes, ext: str, file_path: str) -> tuple[list[DocElement], ParseMeta]:
    """按扩展名分发解析。未知扩展名抛 ValueError；``.doc`` 旧二进制返回 FAILED meta（不抛）。"""
    fmt = DOC_FORMAT_EXTS.get(ext.lower())
    if fmt is None:
        raise ValueError(f"不支持的文档扩展名: {ext!r}")
    if ext.lower() == ".doc":
        # 旧二进制 .doc，python-docx 不支持 → 记 FAILED（DocFile 行可见，交由 per-file try/except）
        return [], ParseMeta(file_format="doc", parse_engine="none",
                             parse_status="FAILED",
                             parse_error="legacy .doc 不支持，请转换为 .docx")
    if fmt in ("markdown", "html"):
        # html 复用 markdown 行级解析（低保真，1.5a 尽力）
        return parse_markdown(decode_text(data), file_path), \
            ParseMeta(file_format=fmt, parse_engine="markdown")
    if fmt == "pdf":
        return parse_pdf(data, file_path)
    if fmt == "docx":
        return parse_docx(data, file_path)
    if fmt == "txt":
        return parse_txt(data, file_path)
    raise ValueError(f"未实现的格式: {fmt}")
