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
    file_format: str  # markdown / pdf / docx / doc / html / txt / xlsx
    parse_engine: str  # markdown / pymupdf / python-docx / text / openpyxl / none
    total_pages: int | None = None
    total_images: int | None = None
    total_tables: int | None = None
    ocr_required: bool = False
    parse_status: str = "COMPLETED"  # COMPLETED / PARTIAL / FAILED
    parse_error: str | None = None
    vision_described: int | None = None   # VISION_DESC on 时实际产出描述的图片数
    vision_skipped: int | None = None     # 超过 VISION_MAX_IMAGES_PER_DOC 被跳过的图片数
