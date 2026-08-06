"""文档切片（设计 §4.2 + §2.6.3）：按 H2/H3 章节切文本，保留 heading_path；
大段代码块(≥5行)独立成 chunk；**表格（Phase 1.5c）独立成 chunk**（结构化 JSON/HTML/描述，
大表 >30 行按 20 行分片，每片带表头上下文）。

chunk_id:
- 文本/代码：``doc_{fileHash8}_{order}``
- 表格：``tbl_{fileHash8}_p{page}_{order}``，分片 ``..._frag{n}``

实现：先把 DocElement 序列切成有序「文本块 / 表格块」（表格打断文本块），再逐块产 spec，
统一 ``order`` 保证文档顺序。
"""
from __future__ import annotations

from app.pipeline.metadata import approx_token_count, content_hash, extract_doc_keywords
from app.pipeline.parsing.doc_element import DocChunkSpec, DocElement

MAX_DOC_TOKENS = 1500
MIN_DOC_TOKENS = 100
SECTION_HEADING_MAX = 3          # H1/H2/H3 作为章节边界
CODE_BLOCK_SEPARATE_LINES = 5    # ≥5 行的代码块独立成 chunk
TABLE_FRAGMENT_ROWS = 30         # 表格行数超过此值则分片
TABLE_FRAGMENT_SIZE = 20         # 每片行数（带表头）


def _split_oversized(text: str, heading_path: list[str]) -> list[str]:
    """超大章节按段落窗口切成多块（每块 ≤ MAX_DOC_TOKENS）。"""
    if approx_token_count(text) <= MAX_DOC_TOKENS:
        return [text]
    paras = [p for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    cur_tokens = 0
    for p in paras:
        t = approx_token_count(p)
        if cur and cur_tokens + t > MAX_DOC_TOKENS:
            chunks.append("\n\n".join(cur))
            cur, cur_tokens = [p], t
        else:
            cur.append(p)
            cur_tokens += t
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks or [text]


# ---------------- 块切分：文本块 / 表格块 ----------------

def _split_blocks(elements: list[DocElement]) -> list[dict]:
    """DocElement 序列 → 有序块（文本块或表格块）。表格打断当前文本块。"""
    blocks: list[dict] = []
    stack: list[tuple[int, str]] = []

    def path() -> list[str]:
        return [t for _, t in stack]

    def new_text(lvl: int | None = None) -> dict:
        return {"is_table": False, "heading_path": path(), "level": lvl, "prose": [],
                "big_code": [], "anchors": [], "page": None}

    def set_page(blk: dict, el_page: int | None) -> None:
        if el_page is not None and blk["page"] is None:
            blk["page"] = el_page

    cur: dict | None = None

    def close_text() -> None:
        nonlocal cur
        if cur is not None and (cur["prose"] or cur["big_code"]):
            blocks.append(cur)
        cur = None

    for el in elements:
        if el.type == "HEADING":
            close_text()
            lvl = el.heading_level or 1
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, el.content))
            cur = new_text(lvl)
            set_page(cur, el.page_number)
        elif el.type == "TABLE":
            close_text()
            blocks.append({"is_table": True, "heading_path": path(),
                           "table_meta": el.metadata, "content": el.content,
                           "page": el.page_number})
            cur = None  # 表格后的文本另开新块
        elif el.type == "ANCHOR":
            if cur is None:
                cur = new_text()
            cur["anchors"].append(el.content)
        elif el.type == "CODE_BLOCK":
            if cur is None:
                cur = new_text()
            if len(el.content.splitlines()) >= CODE_BLOCK_SEPARATE_LINES:
                cur["big_code"].append(el.content)
            else:
                cur["prose"].append(el.content)
            set_page(cur, el.page_number)
        else:  # PARAGRAPH / LIST
            if cur is None:
                cur = new_text()
            if el.content:
                cur["prose"].append(el.content)
                set_page(cur, el.page_number)
    close_text()
    return blocks


# ---------------- spec 产出 ----------------

def _make_spec(chunk_id: str, file_path: str, heading_path: list[str], level: int | None,
               order: int, content: str, anchors: list[str], commit_hash: str,
               *, is_code: bool = False, page_number: int | None = None) -> DocChunkSpec:
    kw = extract_doc_keywords(" ".join(heading_path), content)
    return DocChunkSpec(
        chunk_id=chunk_id,
        file_path=file_path,
        heading_path=heading_path,
        heading_level=level,
        section_order=order,
        content=content,
        content_hash=content_hash(content),
        token_count=approx_token_count(content),
        code_anchors=list(anchors),
        keywords=kw,
        git_commit_hash=commit_hash,
        page_number=page_number,
        chunk_content_type="code_block" if is_code else "text",
    )


def _make_table_spec(*, chunk_id, file_path, heading_path, order, content, commit_hash,
                     page_number, table_data, table_html, table_description,
                     n_rows, n_cols, is_fragment, frag_index, parent) -> DocChunkSpec:
    kw = extract_doc_keywords(" ".join(heading_path), content)
    return DocChunkSpec(
        chunk_id=chunk_id,
        file_path=file_path,
        heading_path=heading_path,
        heading_level=None,
        section_order=order,
        content=content,
        content_hash=content_hash(content),
        token_count=approx_token_count(content),
        code_anchors=[],
        keywords=kw,
        git_commit_hash=commit_hash,
        page_number=page_number,
        chunk_content_type="table_fragment" if is_fragment else "table",
        table_data=table_data,
        table_html=table_html,
        table_description=table_description,
        table_total_rows=n_rows,
        table_total_cols=n_cols,
        is_table_fragment=is_fragment or None,
        table_fragment_index=frag_index,
        parent_table_chunk_id=parent,
    )


def _emit_text(specs: list[DocChunkSpec], blk: dict, fh8: str, file_path: str,
               commit_hash: str, order: int) -> int:
    prose_text = "\n\n".join(p for p in blk["prose"] if p and p.strip())
    anchors = blk["anchors"]
    titles = blk["heading_path"]
    level = blk.get("level")
    page = blk["page"]

    main_texts = _split_oversized(prose_text, titles) if prose_text.strip() else []
    for txt in main_texts:
        specs.append(_make_spec(f"doc_{fh8}_{order}", file_path, titles, level,
                                order, txt, anchors, commit_hash, page_number=page))
        order += 1
    for code in blk["big_code"]:
        specs.append(_make_spec(f"doc_{fh8}_{order}", file_path, titles, level,
                                order, code, anchors, commit_hash, is_code=True, page_number=page))
        order += 1
    return order


def _emit_table(specs: list[DocChunkSpec], blk: dict, fh8: str, file_path: str,
                commit_hash: str, order: int) -> int:
    meta = blk["table_meta"] or {}
    table_data = meta.get("table_data")
    html = meta.get("table_html")
    desc = meta.get("table_description", "")
    n_rows = int(meta.get("n_rows", 0) or 0)
    n_cols = int(meta.get("n_cols", 0) or 0)
    content = blk["content"] or desc
    titles = blk["heading_path"]
    page = blk["page"]
    parent_id = f"tbl_{fh8}_p{page or 0}_{order}"

    headers = (table_data or {}).get("headers") or []
    body_rows = (table_data or {}).get("rows") or []

    # 大表分片：> TABLE_FRAGMENT_ROWS 行，每 TABLE_FRAGMENT_SIZE 行一片（带表头）
    if n_rows > TABLE_FRAGMENT_ROWS and body_rows:
        for fi, start in enumerate(range(0, len(body_rows), TABLE_FRAGMENT_SIZE)):
            frag = body_rows[start:start + TABLE_FRAGMENT_SIZE]
            frag_text = " | ".join(headers) + "\n" + "\n".join(" | ".join(r) for r in frag)
            specs.append(_make_table_spec(
                chunk_id=f"{parent_id}_frag{fi + 1}", file_path=file_path, heading_path=titles,
                order=order, content=f"{desc}\n{frag_text}", commit_hash=commit_hash,
                page_number=page, table_data={"headers": headers, "rows": frag, "n_cols": n_cols},
                table_html=None, table_description=desc, n_rows=len(frag), n_cols=n_cols,
                is_fragment=True, frag_index=fi + 1, parent=parent_id))
            order += 1
        return order

    specs.append(_make_table_spec(
        chunk_id=parent_id, file_path=file_path, heading_path=titles, order=order,
        content=content, commit_hash=commit_hash, page_number=page, table_data=table_data,
        table_html=html, table_description=desc, n_rows=n_rows, n_cols=n_cols,
        is_fragment=False, frag_index=None, parent=None))
    return order + 1


def chunk_doc_elements(elements: list[DocElement], *, file_path: str, file_hash: str,
                       commit_hash: str = "UNKNOWN") -> list[DocChunkSpec]:
    """把 DocElement 列表切成 DocChunkSpec（文本/代码/表格，含大表分片）。"""
    fh8 = file_hash[:8]
    specs: list[DocChunkSpec] = []
    order = 0
    for blk in _split_blocks(elements):
        if blk["is_table"]:
            order = _emit_table(specs, blk, fh8, file_path, commit_hash, order)
        else:
            order = _emit_text(specs, blk, fh8, file_path, commit_hash, order)
    return specs
