"""纯文本解析：按空行分段为 PARAGRAPH DocElement。

``decode_text`` 为 UTF-8 gb18030 latin-1 解码阶梯。
"""
from __future__ import annotations

import re

from app.pipeline.parsing.doc_element import DocElement, ParseMeta

_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


def decode_text(data: bytes) -> str:
    """UTF-8 gb18030 latin-1(errors=replace) 解码阶梯。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def parse_txt(data: bytes, file_path: str) -> tuple[list[DocElement], ParseMeta]:
    text = decode_text(data)
    blocks = [b.strip() for b in _PARA_SPLIT_RE.split(text) if b.strip()]
    elements = [DocElement(type="PARAGRAPH", content=b) for b in blocks]
    return elements, ParseMeta(file_format="txt", parse_engine="text")
