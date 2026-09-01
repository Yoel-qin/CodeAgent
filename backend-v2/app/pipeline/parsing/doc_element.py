"""文档解析中间结构：DocElement（多格式统一）+ ParseMeta（解析器元信息）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DocElement:
    """多格式文档统一中间结构。"""
    type: str  # HEADING / PARAGRAPH / TABLE / IMAGE / CODE_BLOCK / LIST
    content: str
    heading_path: list[str] = field(default_factory=list)
    heading_level: int | None = None
    page_number: int | None = None
    bbox: dict | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ParseMeta:
    """解析器附加元信息。"""
    file_format: str  # markdown / pdf / docx / doc / html / txt
    parse_engine: str  # markdown / pymupdf / python-docx / text / none
    total_pages: int | None = None
    total_images: int | None = None
    total_tables: int | None = None
    ocr_required: bool = False
    parse_status: str = "COMPLETED"  # COMPLETED / PARTIAL / FAILED
    parse_error: str | None = None
