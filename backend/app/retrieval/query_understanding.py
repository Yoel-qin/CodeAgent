"""Stage 0 查询理解（设计 §11.2）：

- extract_query_terms：规则版检索词提取（英文词 + 中文段 + camelCase 拆分），BM25/词法召回用。
- rewrite_query：LLM 查询改写（语义查询 + 补充关键词）；未配置/失败优雅降级为原 query + 空 keywords。
"""
from __future__ import annotations

import re

from app.clients.llm_client import llm
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


_REWRITE_SYS = (
    "你是代码/文档知识库的检索查询改写助手。把用户问题改写为更适合语义向量检索的查询"
    "（补全指代、消除歧义、保留原意，中英均可），并给出若干补充关键词（英文标识符/技术名词）。"
)
_REWRITE_USER = (
    "用户问题：{q}\n"
    "请严格按以下两行输出，不要编号、不要解释：\n"
    "QUERY: <改写后的查询>\n"
    "KEYWORDS: <逗号分隔的补充关键词，无可留空>"
)


async def rewrite_query(query: str) -> dict:
    """Stage0 LLM 查询改写：返回 {"semantic_query", "extra_keywords"}。
    未配置 LLM / 调用失败 / 解析失败 → 退化为原 query + 空 keywords（主链路不中断）。
    """
    fallback = {"semantic_query": query, "extra_keywords": []}
    if not llm.configured:
        return fallback
    try:
        text = await llm.chat(
            [
                {"role": "system", "content": _REWRITE_SYS},
                {"role": "user", "content": _REWRITE_USER.format(q=query)},
            ],
            temperature=0.2,
            max_tokens=256,
        )
    except Exception:
        return fallback

    sem = query
    kws: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        upper = s.upper()
        if upper.startswith("QUERY:") and ":" in s:
            sem = s.split(":", 1)[1].strip() or query
        elif upper.startswith("KEYWORDS:") and ":" in s:
            raw = s.split(":", 1)[1].strip()
            kws = [k.strip() for k in raw.replace("，", ",").split(",") if k.strip()]
    return {"semantic_query": sem, "extra_keywords": kws}
