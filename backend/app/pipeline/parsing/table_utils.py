"""表格结构化共享工具（Phase 1.5c）。

把「逻辑行」``list[list[(text, col_span)]]`` 或 PDF 网格 ``list[list[str|None]]``
归一为结构化 JSON + HTML（含 colspan）+ 自然语言描述（模板，同步、免 LLM）。

- Word：经 python-docx 的 ``_tc`` 标识识别横向合并（col_span）；纵向合并（vMerge）留后续。
- PDF：PyMuPDF ``find_tables().extract()`` 给出网格（None 表空/合并占位），col_span 暂全 1。
"""
from __future__ import annotations

from html import escape

# 逻辑行：每行是若干 (text, col_span) 元组
LogicalRow = list[tuple[str, int]]


def grid_to_logical(grid: list[list[str | None]]) -> list[LogicalRow]:
    """PDF 网格 → 逻辑行（col_span 全 1，None→''）。"""
    return [[((c or "").strip(), 1) for c in row] for row in grid]


def logical_to_struct(logical_rows: list[LogicalRow], n_rows: int, n_cols: int) -> tuple[dict, str, str]:
    """逻辑行 → (table_data, html, description)。首行作表头。"""
    headers = [t for t, _ in (logical_rows[0] if logical_rows else [])]
    body = [[t for t, _ in r] for r in logical_rows[1:]]
    col_spans = [[s for _, s in r] for r in logical_rows]
    table_data = {
        "headers": headers,
        "rows": body,
        "col_spans": col_spans,
        "n_rows": n_rows,
        "n_cols": n_cols,
    }
    html = _to_html(logical_rows)
    desc = _describe(n_rows, n_cols, headers)
    return table_data, html, desc


def _cell(tag: str, text: str, span: int) -> str:
    attr = f' colspan="{span}"' if span > 1 else ""
    return f"<{tag}{attr}>{escape(text)}</{tag}>"


def _to_html(logical_rows: list[LogicalRow]) -> str:
    if not logical_rows:
        return ""

    def tr(cells: LogicalRow, tag: str) -> str:
        return "<tr>" + "".join(_cell(tag, t, s) for t, s in cells) + "</tr>"

    head = "<thead>" + tr(logical_rows[0], "th") + "</thead>"
    body = "<tbody>" + "".join(tr(r, "td") for r in logical_rows[1:]) + "</tbody>"
    return f"<table>{head}{body}</table>"


def _describe(n_rows: int, n_cols: int, headers: list[str]) -> str:
    """模板化表格描述（向量化用；LLM 增强留后续）。"""
    h = "、".join(headers[:8]) if headers else ""
    return f"{n_rows} 行 × {n_cols} 列表格。" + (f"列：{h}。" if h else "")


def to_text_dump(logical_rows: list[LogicalRow]) -> str:
    """逻辑行 → 纯文本（用于 content 全文索引）。"""
    lines = [" | ".join(t for t, _ in r) for r in logical_rows]
    return "\n".join(lines)
