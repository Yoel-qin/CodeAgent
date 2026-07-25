"""关联构建（设计 §6）：
- 锚点匹配：CODE_ANCHOR → DOC_TO_CODE / CODE_TO_DOC + anchor_mappings（置信度 1.0）
- 调用图：方法调用表达式 → call_graph 边（Phase 1 仅同类内简单名解析；
  跨类/类型感知解析留待后续，note）

幂等：每次全量重建（先删后插）。
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import AnchorMapping, CallGraph, ChunkRelation, CodeChunk, DocChunk
from app.pipeline.parsing.code_parser import parse_java


def build_anchor_relations(session: Session) -> dict:
    """根据 code_chunks.code_anchor_key 与 doc_chunks.code_anchors 精确匹配建关联。"""
    code_chunks = session.execute(
        select(CodeChunk).where(CodeChunk.is_deleted == False)  # noqa: E712
    ).scalars().all()
    doc_chunks = session.execute(
        select(DocChunk).where(DocChunk.is_deleted == False)  # noqa: E712
    ).scalars().all()

    key2code: dict[str, list[str]] = defaultdict(list)
    for c in code_chunks:
        if c.code_anchor_key:
            key2code[c.code_anchor_key].append(c.chunk_id)

    # 清空旧关联（全量重建）
    session.execute(delete(ChunkRelation).where(
        ChunkRelation.relation_type.in_(["DOC_TO_CODE", "CODE_TO_DOC"])))
    session.execute(delete(AnchorMapping))
    session.flush()

    rel_set: set[tuple[str, str, str]] = set()
    map_set: set[tuple[str, str, str]] = set()
    unmatched: list[str] = []
    for d in doc_chunks:
        for ak in (d.code_anchors or []):
            targets = key2code.get(ak)
            if not targets:
                unmatched.append(ak)
                continue
            for cid in targets:
                rel_set.add((d.chunk_id, cid, "DOC_TO_CODE"))
                rel_set.add((cid, d.chunk_id, "CODE_TO_DOC"))
                map_set.add((ak, cid, d.chunk_id))

    for s, t, rt in rel_set:
        session.add(ChunkRelation(source_chunk_id=s, target_chunk_id=t,
                                  relation_type=rt, confidence=1.0))
    for ak, cid, did in map_set:
        session.add(AnchorMapping(anchor_key=ak, code_chunk_id=cid,
                                  doc_chunk_id=did, is_active=True))
    session.flush()
    return {
        "relations": len(rel_set),
        "anchor_mappings": len(map_set),
        "unmatched_anchors": len(unmatched),
    }


def build_call_graph(session: Session, repo_path: str | Path) -> dict:
    """从仓库源码重新解析调用表达式 → call_graph 边（同类内简单名解析）。

    calls 未落库，故此处重解析仓库；方法 → chunk_id 经 code_anchor_key 映射。
    """
    code_chunks = session.execute(
        select(CodeChunk).where(
            CodeChunk.is_deleted == False,  # noqa: E712
            CodeChunk.chunk_type == "method",
        )
    ).scalars().all()

    by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_key: dict[str, list[str]] = defaultdict(list)
    for c in code_chunks:
        if c.class_name and c.method_name:
            by_pair[(c.class_name, c.method_name)].append(c.chunk_id)
        if c.code_anchor_key:
            by_key[c.code_anchor_key].append(c.chunk_id)

    session.execute(delete(CallGraph))
    session.flush()

    edges: set[tuple[str, str, str, bool]] = set()
    repo = Path(repo_path)
    if repo.exists():
        for f in sorted(repo.rglob("*.java")):
            src = f.read_text(encoding="utf-8", errors="replace")
            pf = parse_java(src, str(f))
            for cls in pf.classes:
                for m in cls.methods:
                    caller_ids = by_key.get(f"{cls.name}.{m.name}")
                    if not caller_ids:
                        continue
                    caller_id = caller_ids[0]
                    for call in m.calls:
                        callees = by_pair.get((cls.name, call))
                        if not callees:
                            continue
                        for cid in callees:
                            if cid == caller_id:
                                edges.add((caller_id, cid, call, True))
                            else:
                                edges.add((caller_id, cid, call, False))

    for caller, callee, expr, recursive in edges:
        session.add(CallGraph(caller_chunk_id=caller, callee_chunk_id=callee,
                              call_expression=expr, is_recursive=recursive))
    session.flush()
    return {"call_edges": len(edges)}


def build_all(session: Session, repo_path: str | Path | None = None) -> dict:
    stats = {"anchors": build_anchor_relations(session)}
    if repo_path:
        stats["call_graph"] = build_call_graph(session, repo_path)
    return stats
