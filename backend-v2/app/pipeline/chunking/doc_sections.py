"""section 组装：DocChunkSpec → doc_sections 行 dict（anchor slug 化 + 撞名消歧）。
"""
from __future__ import annotations

import re

from app.pipeline.chunking.doc_chunker import DocChunkSpec

# slug 化正则：只保留 \w、CJK 统一汉字、连字符
_SLUG_RE = re.compile(r"[^\w一-鿿-]+")


def _slugify(text: str) -> str:
    """单个 heading 片段 slug 化：lowercase + 非法字符替换为 `-` + 去首尾 `-`。"""
    return re.sub(_SLUG_RE, "-", text.strip().lower()).strip("-")


def _make_anchor(spec: DocChunkSpec) -> str:
    """从 heading_path 生成 anchor slug。

    公式: 各段 slug 化（保留 word/CJK/连字符，其余替换为 "-"）再 "/" join
    空 heading_path 回退 "sec-{order_index}"。
    """
    if spec.heading_path:
        parts = [_slugify(h) for h in spec.heading_path]
        return "/".join(p for p in parts if p) or f"sec-{spec.order_index}"
    return f"sec-{spec.order_index}"


def build_doc_rows(
    specs: list[DocChunkSpec], *, document_id: int, repo: str
) -> tuple[list[dict], list[dict]]:
    """DocChunkSpec 列表 → (section_rows, media_rows) 纯函数。

    section_row dict 字段对齐 Task 1 DocSection 列：
      document_id / repo / anchor / title / level / kind / content /
      token_count / order_index / page / embedding_synced

    anchor 生成：
      - heading_path 各段 slug 化后 "/" 拼接
      - 空 heading_path → "sec-{order_index}"
      - 同文档内撞名追加 "-2" / "-3" 递增消歧

    media_rows：v1 doc_chunker 不产 IMAGE spec，恒空列表。
    图片段由 Task 5 ingest 直接从 DocElement 采集（不经过 chunk_doc_elements）。
    签名保留 (section_rows, media_rows) 以对称。
    """
    section_rows: list[dict] = []
    # 同文档内 anchor 撞名计数
    anchor_counts: dict[str, int] = {}

    for spec in specs:
        base_anchor = _make_anchor(spec)
        anchor_counts[base_anchor] = anchor_counts.get(base_anchor, 0) + 1

    # 第二遍：生成去重 anchor
    anchor_seen: dict[str, int] = {}
    deduped_anchors: list[str] = []
    for spec in specs:
        base_anchor = _make_anchor(spec)
        if anchor_counts[base_anchor] > 1:
            anchor_seen[base_anchor] = anchor_seen.get(base_anchor, 0) + 1
            anchor = f"{base_anchor}-{anchor_seen[base_anchor]}" if anchor_seen[base_anchor] > 1 else base_anchor
        else:
            anchor = base_anchor
        deduped_anchors.append(anchor)

    for spec, anchor in zip(specs, deduped_anchors):
        title = spec.heading_path[-1] if spec.heading_path else ""
        section_rows.append({
            "document_id": document_id,
            "repo": repo,
            "anchor": anchor,
            "title": title,
            "level": spec.level,
            "kind": spec.kind,
            "content": spec.content,
            "token_count": spec.token_count,
            "order_index": spec.order_index,
            "page": spec.page,
            "embedding_synced": False,
        })

    # v1 doc_chunker 不产 IMAGE spec → media_rows 恒空
    return section_rows, []
