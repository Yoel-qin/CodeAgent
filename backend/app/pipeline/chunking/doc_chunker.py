"""文档切片（设计 §4.2）：按 H2/H3 章节切，保留 heading_path，
大段代码块(≥5行)独立成 chunk，超大章节按段落窗口二次切分。
chunk_id: doc_{fileHash8}_{order}
"""
from __future__ import annotations

from app.pipeline.metadata import approx_token_count, content_hash, extract_doc_keywords, short_hash
from app.pipeline.parsing.doc_element import DocChunkSpec, DocElement

MAX_DOC_TOKENS = 1500
MIN_DOC_TOKENS = 100
SECTION_HEADING_MAX = 3          # H1/H2/H3 作为章节边界
CODE_BLOCK_SEPARATE_LINES = 5    # ≥5 行的代码块独立成 chunk


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


def chunk_doc_elements(elements: list[DocElement], *, file_path: str, file_hash: str,
                       commit_hash: str = "UNKNOWN") -> list[DocChunkSpec]:
    """把 DocElement 列表切成 DocChunkSpec。"""
    sections: list[dict] = []
    cur: dict | None = None
    stack: list[tuple[int, str]] = []

    def start_section(level: int) -> None:
        nonlocal cur
        if cur is not None:
            sections.append(cur)
        cur = {
            "heading_path": [t for _, t in stack],
            "level": level,
            "prose": [],
            "anchors": [],
            "big_code": [],
        }

    start_section(0)  # preamble
    for el in elements:
        if el.type == "HEADING":
            lvl = el.heading_level or 1
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, el.content))
            if lvl <= SECTION_HEADING_MAX:
                start_section(lvl)
            else:
                # H4+ 不开新节，但更新当前节路径上下文（并入 prose 作为小标题）
                if cur is not None:
                    cur["prose"].append(el.content)
        elif el.type == "ANCHOR":
            if cur is not None:
                cur["anchors"].append(el.content)
        elif el.type == "CODE_BLOCK":
            if cur is None:
                continue
            if len(el.content.splitlines()) >= CODE_BLOCK_SEPARATE_LINES:
                cur["big_code"].append(el.content)
            else:
                cur["prose"].append(el.content)
        else:  # PARAGRAPH / TABLE / LIST
            if cur is not None and el.content:
                cur["prose"].append(el.content)
    if cur is not None:
        sections.append(cur)

    specs: list[DocChunkSpec] = []
    order = 0
    fh8 = file_hash[:8]
    for sec in sections:
        prose_text = "\n\n".join(p for p in sec["prose"] if p and p.strip())
        anchors = sec["anchors"]
        titles = sec["heading_path"]
        level = sec["level"]

        # 主章节 chunk（可能有多个，若超大）
        main_texts = _split_oversized(prose_text, titles) if prose_text.strip() else []
        for txt in main_texts:
            specs.append(_make_spec(
                f"doc_{fh8}_{order}", file_path, titles, level or None,
                order, txt, anchors, commit_hash,
            ))
            order += 1

        # 大代码块独立 chunk（继承章节路径，content=代码）
        for code in sec["big_code"]:
            specs.append(_make_spec(
                f"doc_{fh8}_{order}", file_path, titles, level or None,
                order, code, anchors, commit_hash, is_code=True,
            ))
            order += 1

    # 过小 chunk 合并标记（此处仅保留，合并逻辑可后续增强）
    return specs


def _make_spec(chunk_id: str, file_path: str, heading_path: list[str], level: int | None,
               order: int, content: str, anchors: list[str], commit_hash: str,
               *, is_code: bool = False) -> DocChunkSpec:
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
    )
