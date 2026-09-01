"""Markdown 解析：行级解析为 DocElement 列表，保留标题层级与 heading_path 栈追踪。
"""
from __future__ import annotations

import re

from app.pipeline.parsing.doc_element import DocElement

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_ANCHOR_RE = re.compile(r"CODE_ANCHOR:\s*([A-Za-z_][\w.]*)")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE_TICKS = ("```", "~~~")


def parse_markdown(source: str, file_path: str) -> list[DocElement]:
    lines = source.splitlines()
    elements: list[DocElement] = []
    para_buf: list[str] = []
    table_buf: list[str] = []
    heading_stack: list[str] = []  # heading_path 栈追踪
    i, n = 0, len(lines)

    def flush_para() -> None:
        if para_buf:
            txt = "\n".join(para_buf).strip()
            if txt:
                elements.append(
                    DocElement(type="PARAGRAPH", content=txt, heading_path=list(heading_stack))
                )
            para_buf.clear()

    def flush_table() -> None:
        if table_buf:
            elements.append(
                DocElement(type="TABLE", content="\n".join(table_buf), heading_path=list(heading_stack))
            )
            table_buf.clear()

    while i < n:
        line = lines[i]

        # fenced code block
        stripped = line.lstrip()
        if stripped[:3] in _FENCE_TICKS:
            flush_para()
            flush_table()
            fence = stripped[:3]
            buf = [line]
            i += 1
            while i < n:
                buf.append(lines[i])
                if lines[i].lstrip().startswith(fence):
                    i += 1
                    break
                i += 1
            elements.append(
                DocElement(type="CODE_BLOCK", content="\n".join(buf), heading_path=list(heading_stack))
            )
            continue

        # heading
        m = _HEADING_RE.match(line)
        if m:
            flush_para()
            flush_table()
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            # 维护 heading_path 栈：弹出同级或更深的，压入当前
            while len(heading_stack) >= level:
                heading_stack.pop()
            heading_stack.append(heading_text)
            elements.append(
                DocElement(type="HEADING", content=heading_text, heading_level=level,
                           heading_path=list(heading_stack[:-1]))
            )
            i += 1
            continue

        # CODE_ANCHOR (HTML comment)
        am = _ANCHOR_RE.search(line)
        if am and "<!--" in line:
            flush_para()
            elements.append(DocElement(type="ANCHOR", content=am.group(1)))
            remainder = _HTML_COMMENT_RE.sub("", line).strip()
            if remainder:
                para_buf.append(remainder)
            i += 1
            continue

        # table row
        if line.strip().startswith("|"):
            flush_para()
            table_buf.append(line)
            i += 1
            continue
        flush_table()

        # paragraph
        if line.strip() == "":
            flush_para()
        else:
            para_buf.append(line)
        i += 1

    flush_para()
    flush_table()
    return elements
