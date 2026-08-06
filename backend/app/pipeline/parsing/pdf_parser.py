"""PDF 文本解析（Phase 1.5a，PyMuPDF/fitz）。

- ``page.get_text("dict")`` 取 span 文本 / 字号 / 加粗标志。
- **正文字号** = 全文档 span 字号的众数；行字号按倍率判 HEADING 层级
  （≥body×1.5→H1、×1.3→H2、×1.15 或加粗且 >body→H3）。
- 同页连续正文行合并为一段 PARAGRAPH；每个元素带 ``page_number``。
- **扫描页检测**：页面可抽文本 < ``_MIN_TEXT`` 字符判为扫描页 → ``ocr_required=True``、
  ``parse_status="PARTIAL"``（**不做 OCR**，留待后续子阶段）。

图片提取、表格结构化（JSON/合并单元格）延迟到 1.5b/1.5c；本阶段表格文本按正文落。
"""
from __future__ import annotations

from collections import Counter

import fitz

from app.pipeline.parsing.doc_element import DocElement, ParseMeta
from app.pipeline.parsing.table_utils import grid_to_logical, logical_to_struct, to_text_dump

_MIN_TEXT = 20  # 页面可抽文本字符数阈值，低于此判为扫描页


def _mode(xs: list[float]) -> float:
    """众数作正文字号；计数并列时取**较小**者（正文比标题小，避免小文档里标题被误判为正文）。"""
    if not xs:
        return 0.0
    cnt = Counter(xs)
    top = cnt.most_common(1)[0][1]
    return min(s for s, c in cnt.items() if c == top)


def _heading_level(size: float, body: float, bold: bool) -> int | None:
    if body <= 0:
        return None
    ratio = size / body
    if ratio >= 1.5:
        return 1
    if ratio >= 1.3:
        return 2
    if ratio >= 1.15 or (bold and size > body):
        return 3
    return None


def _flush(buf: list[str], elements: list[DocElement], page: int) -> None:
    if buf:
        elements.append(DocElement(type="PARAGRAPH", content=" ".join(buf).strip(),
                                   page_number=page))
        buf.clear()


def parse_pdf(data: bytes, file_path: str) -> tuple[list[DocElement], ParseMeta]:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        # 第一遍：收集 span 字号求众数（正文字号）
        sizes: list[float] = []
        for page in doc:
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            sizes.append(round(float(span.get("size", 0)), 1))
        body = _mode(sizes) if sizes else 12.0

        elements: list[DocElement] = []
        scanned_pages = 0
        # 第二遍：逐页生成元素
        for pno, page in enumerate(doc):
            page_no = pno + 1
            if len((page.get_text("text") or "").strip()) < _MIN_TEXT:
                scanned_pages += 1
            # 展平 blocks→lines，逐行判标题/正文
            lines: list[tuple[str, float, bool]] = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    ltext = "".join(s.get("text", "") for s in spans).strip()
                    if not ltext:
                        continue
                    size = max((round(float(s.get("size", 0)), 1) for s in spans), default=0.0)
                    bold = any(bool(s.get("flags", 0) & 16) for s in spans)
                    lines.append((ltext, size, bold))
            buf: list[str] = []
            for ltext, size, bold in lines:
                lvl = _heading_level(size, body, bold)
                if lvl is not None:
                    _flush(buf, elements, page_no)
                    elements.append(DocElement(type="HEADING", content=ltext,
                                               heading_level=lvl, page_number=page_no))
                else:
                    buf.append(ltext)
            _flush(buf, elements, page_no)

            # 表格（Phase 1.5c，PyMuPDF find_tables，best-effort；与正文顺序近似）
            try:
                for tf in page.find_tables().tables:
                    grid = tf.extract()
                    if not grid or not any(any(c for c in row) for row in grid):
                        continue
                    logical = grid_to_logical(grid)
                    n_rows, n_cols = len(grid), max((len(r) for r in grid), default=0)
                    table_data, html, desc = logical_to_struct(logical, n_rows, n_cols)
                    elements.append(DocElement(
                        type="TABLE",
                        content=f"{desc}\n{to_text_dump(logical)}",
                        page_number=page_no,
                        metadata={"table_data": table_data, "table_html": html,
                                  "table_description": desc, "n_rows": n_rows, "n_cols": n_cols},
                    ))
            except Exception:
                pass

            # 图片（Phase 1.5b，PyMuPDF extract_image）
            try:
                for im in page.get_images(full=True):
                    try:
                        info = doc.extract_image(im[0])
                    except Exception:
                        continue
                    raw = info.get("image")
                    if not raw:
                        continue
                    elements.append(DocElement(
                        type="IMAGE", content="", page_number=page_no,
                        metadata={"image_bytes": raw, "ext": info.get("ext", "png"),
                                  "width": info.get("width"), "height": info.get("height")},
                    ))
            except Exception:
                pass

        ocr = scanned_pages > 0
        status = "PARTIAL" if ocr else "COMPLETED"
        meta = ParseMeta(
            file_format="pdf", parse_engine="pymupdf", total_pages=doc.page_count,
            ocr_required=ocr, parse_status=status,
            parse_error=(f"scanned pages: {scanned_pages}" if ocr else None),
        )
        return elements, meta
    finally:
        doc.close()
