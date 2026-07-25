"""Stage 0 查询理解（设计 §11.2）：提取检索词（英文词 + 中文段 + camelCase 拆分）。

Phase 1 为无 LLM 版本（关键词/实体提取）；LLM 改写/意图分类在 Phase 7 Agent 接入。
"""
from __future__ import annotations

import re

from app.pipeline.metadata import extract_doc_keywords, split_identifier

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")


def extract_query_terms(query: str, max_n: int = 16) -> list[str]:
    """提取查询检索词：文档式分词 + 代码标识符 camelCase 拆分。"""
    base = extract_doc_keywords(query, max_n=max_n)
    extra: list[str] = []
    for tok in _TOKEN_RE.findall(query):
        extra.extend(split_identifier(tok))
    seen: dict[str, None] = {}
    for t in base + extra:
        if len(t) >= 2:
            seen.setdefault(t.lower(), None)
    return list(seen.keys())[:max_n]
