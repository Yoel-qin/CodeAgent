"""Markdown 解析（设计 §3.2）：行级解析为 DocElement 列表，保留标题层级，
识别 `<!-- CODE_ANCHOR: ClassName.methodName -->` 标记。

实现为轻量行级解析（无第三方依赖），覆盖标题/ fenced 代码块/表格/列表/锚点，
足够处理本项目设计文档；复杂表格结构化留给 Phase 1.5 多格式管道。
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
    i, n = 0, len(lines)

    def flush_para() -> None:
        if para_buf:
            txt = "\n".join(para_buf).strip()
            if txt:
                elements.append(DocElement(type="PARAGRAPH", content=txt))
            para_buf.clear()

    def flush_table() -> None:
        if table_buf:
            elements.append(DocElement(type="TABLE", content="\n".join(table_buf)))
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
            elements.append(DocElement(type="CODE_BLOCK", content="\n".join(buf)))
            continue

        # heading
        m = _HEADING_RE.match(line)
        if m:
            flush_para()
            flush_table()
            elements.append(DocElement(type="HEADING", content=m.group(2).strip(),
                                       heading_level=len(m.group(1))))
            i += 1
            continue

        # CODE_ANCHOR (HTML comment)
        am = _ANCHOR_RE.search(line)
        if am and "<!--" in line:
            flush_para()
            elements.append(DocElement(type="ANCHOR", content=am.group(1)))
            # 保留注释行外的文本
            remainder = _HTML_COMMENT_RE.sub("", line).strip()
            if remainder:
                para_buf.append(remainder)
            i += 1
            continue

        # table row (以 | 开头的连续行)
        if line.strip().startswith("|"):
            flush_para()
            table_buf.append(line)
            i += 1
            continue
        flush_table()

        # 段落
        if line.strip() == "":
            flush_para()
        else:
            para_buf.append(line)
        i += 1

    flush_para()
    flush_table()
    return elements
