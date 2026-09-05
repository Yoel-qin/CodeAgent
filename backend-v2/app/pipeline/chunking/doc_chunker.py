"""文档切片（从旧库移植）：按 H2/H3 章节切文本，保留 heading_path；大段代码块(≥5行)独立成 chunk；表格独立成 chunk（大表 >30 行按 20 行分片）；已描述图片独立成 chunk。

chunk_id:
- 文本/代码：``doc_{fileHash8}_{order}``
- 表格：``tbl_{fileHash8}_p{page}_{order}``，分片 ``..._frag{n}``
- 图片（已描述）：``img_{fileHash8}_{order}``

v2 裁剪：DocChunkSpec 只保留文档入库用字段（去掉 keywords/code_anchors/content_hash/table 结构化字段/image 字段），
approx_token_count 内联（无 jieba 依赖）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.pipeline.parsing.doc_element import DocElement

MAX_DOC_TOKENS = 1500
MIN_DOC_TOKENS = 100
SECTION_HEADING_MAX = 3          # H1/H2/H3 作为章节边界
CODE_BLOCK_SEPARATE_LINES = 5    # ≥5 行的代码块独立成 chunk
TABLE_FRAGMENT_ROWS = 30         # 表格行数超过此值则分片
TABLE_FRAGMENT_SIZE = 20         # 每片行数（带表头）


# ---------------------------------------------------------------------------
# v2 DocChunkSpec：只保留文档入库用字段
# ---------------------------------------------------------------------------

@dataclass
class DocChunkSpec:
    """文档 chunk 规格字段（v2 裁剪版，对齐 doc_sections 表）。

    裁剪自旧库 DocChunkSpec，去掉 keywords/code_anchors/content_hash/
    table 结构化字段/image 字段——这些在 v2 中要么不需要，要么由其他路径处理。
    保留 heading_path/level/page 以支持 section 组装和前端渲染。
    """
    chunk_id: str
    file_path: str
    heading_path: list[str] = field(default_factory=list)
    level: int | None = None
    kind: str = "text"                 # text / table / image
    content: str = ""
    page: int | None = None
    token_count: int = 0
    order_index: int = 0
    commit_hash: str = "UNKNOWN"


# ---------------------------------------------------------------------------
# 内联元数据工具（无外部依赖）
# ---------------------------------------------------------------------------

def _approx_token_count(text: str) -> int:
    """粗略 token 估算：英文 ~4 字符/token，中文按字算。仅用于切片大小控制。"""
    words = len(text.split())
    by_char = len(text) // 4
    return max(int(words * 1.3), by_char, 1)


# ---------------------------------------------------------------------------
# 块切分
# ---------------------------------------------------------------------------

def _split_oversized(text: str, heading_path: list[str]) -> list[str]:
    """超大章节按段落窗口切成多块（每块 ≤ MAX_DOC_TOKENS）。"""
    if _approx_token_count(text) <= MAX_DOC_TOKENS:
        return [text]
    paras = [p for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    cur_tokens = 0
    for p in paras:
        t = _approx_token_count(p)
        if cur and cur_tokens + t > MAX_DOC_TOKENS:
            chunks.append("\n\n".join(cur))
            cur, cur_tokens = [p], t
        else:
            cur.append(p)
            cur_tokens += t
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks or [text]


def _split_blocks(elements: list[DocElement]) -> list[dict]:
    """DocElement 序列 → 有序块（文本块/表格块/图片块）。表格与图片打断当前文本块。"""
    blocks: list[dict] = []
    stack: list[tuple[int, str]] = []
    img_count = 0

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
        elif el.type == "IMAGE" and el.content:
            # 已描述图片独立成块（描述由 ingest 的 VISION_DESC 注入 el.content；
            # 空描述 IMAGE 不产块——现状保持）。img_count 只数已描述图，与 ingest
            # 的 described 计数一一对应（spec §3.4）
            close_text()
            img_count += 1
            blocks.append({"is_table": False, "is_image": True, "heading_path": [],
                           "content": el.content, "page": el.page_number,
                           "img_seq": img_count})
            cur = None
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


# ---------------------------------------------------------------------------
# spec 产出
# ---------------------------------------------------------------------------

def _make_spec(chunk_id: str, file_path: str, heading_path: list[str], level: int | None,
               order: int, content: str, commit_hash: str,
               *, page_number: int | None = None) -> DocChunkSpec:
    return DocChunkSpec(
        chunk_id=chunk_id,
        file_path=file_path,
        heading_path=heading_path,
        level=level,
        kind="text",
        content=content,
        page=page_number,
        token_count=_approx_token_count(content),
        order_index=order,
        commit_hash=commit_hash,
    )


def _make_typed_spec(*, kind: str, chunk_id, file_path, heading_path, order, content,
                     commit_hash, page_number) -> DocChunkSpec:
    """table / image 共用的非文本 spec 构造（level 恒 None）。"""
    return DocChunkSpec(
        chunk_id=chunk_id,
        file_path=file_path,
        heading_path=heading_path,
        level=None,
        kind=kind,
        content=content,
        page=page_number,
        token_count=_approx_token_count(content),
        order_index=order,
        commit_hash=commit_hash,
    )


def _emit_text(specs: list[DocChunkSpec], blk: dict, fh8: str, file_path: str,
               commit_hash: str, order: int) -> int:
    prose_text = "\n\n".join(p for p in blk["prose"] if p and p.strip())
    titles = blk["heading_path"]
    level = blk.get("level")
    page = blk["page"]

    main_texts = _split_oversized(prose_text, titles) if prose_text.strip() else []
    for txt in main_texts:
        specs.append(_make_spec(f"doc_{fh8}_{order}", file_path, titles, level,
                                order, txt, commit_hash, page_number=page))
        order += 1
    for code in blk["big_code"]:
        specs.append(_make_spec(f"doc_{fh8}_{order}", file_path, titles, level,
                                order, code, commit_hash, page_number=page))
        order += 1
    return order


def _emit_table(specs: list[DocChunkSpec], blk: dict, fh8: str, file_path: str,
                commit_hash: str, order: int) -> int:
    meta = blk["table_meta"] or {}
    desc = meta.get("table_description", "")
    n_rows = int(meta.get("n_rows", 0) or 0)
    table_data = meta.get("table_data")
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
            specs.append(_make_typed_spec(
                kind="table", chunk_id=f"{parent_id}_frag{fi + 1}", file_path=file_path,
                heading_path=titles, order=order, content=f"{desc}\n{frag_text}",
                commit_hash=commit_hash, page_number=page))
            order += 1
        return order

    specs.append(_make_typed_spec(
        kind="table", chunk_id=parent_id, file_path=file_path, heading_path=titles,
        order=order, content=content, commit_hash=commit_hash, page_number=page))
    return order + 1


def _emit_image(specs: list[DocChunkSpec], blk: dict, fh8: str, file_path: str,
                commit_hash: str, order: int) -> int:
    """已描述图片 → kind="image" spec（title=图 n：描述前缀 40 字，作 anchor 源）。"""
    desc = blk["content"] or ""
    title = f"图 {blk['img_seq']}：{desc[:40]}"
    specs.append(_make_typed_spec(
        kind="image", chunk_id=f"img_{fh8}_{order}", file_path=file_path,
        heading_path=[title], order=order, content=desc, commit_hash=commit_hash,
        page_number=blk["page"]))
    return order + 1


def chunk_doc_elements(elements: list[DocElement], *, file_path: str, file_hash: str,
                       commit_hash: str = "UNKNOWN") -> list[DocChunkSpec]:
    """把 DocElement 列表切成 DocChunkSpec（文本/代码/表格/已描述图片，含大表分片）。"""
    fh8 = file_hash[:8]
    specs: list[DocChunkSpec] = []
    order = 0
    for blk in _split_blocks(elements):
        if blk["is_table"]:
            order = _emit_table(specs, blk, fh8, file_path, commit_hash, order)
        elif blk.get("is_image"):
            order = _emit_image(specs, blk, fh8, file_path, commit_hash, order)
        else:
            order = _emit_text(specs, blk, fh8, file_path, commit_hash, order)
    return specs
