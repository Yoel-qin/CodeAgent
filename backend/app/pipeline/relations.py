"""关联构建（设计 §6）：
- 锚点匹配：CODE_ANCHOR → DOC_TO_CODE / CODE_TO_DOC + anchor_mappings（置信度 1.0）
- 调用图：方法调用表达式 → call_graph 边（M46 起跨类四步解析：
  ① receiver 定型（参数>字段>局部变量，miss 则 receiver 名当类名=静态调用）
  ② 类型简单名匹配 class_name ③ 继承闭包分发到后代实现类 ④ this/super/无 receiver 同类）

幂等：每次全量重建（先删后插）。
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import AnchorMapping, CallGraph, ChunkRelation, CodeChunk, DocChunk
from app.pipeline.parsing.code_parser import parse_java
from app.pipeline.parsing.doc_element import CodeClass, CodeMethod


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


# ============================================================================
# M46 跨类调用解析（纯函数，单测覆盖）：定型 → 类匹配 → 继承闭包分发
# ============================================================================

_MODIFIER_TOKENS = {"final", "transient", "volatile", "static"}
_GENERIC_RE = re.compile(r"<[^<>]*>")


def _simple_name(type_text: str) -> str:
    """"final Map<String, String>" / "org.acme.Foo[]" → "Map"/"Foo"。"""
    t = _GENERIC_RE.sub("", type_text).split("[", 1)[0].strip()
    toks = [x for x in t.split() if x not in _MODIFIER_TOKENS]
    if not toks:
        return ""
    return toks[-1].rsplit(".", 1)[-1]


def _param_types(m: CodeMethod) -> dict[str, str]:
    """参数原文（"final MessageStore store" / "@A Map<K,V> cache"）→ {变量名: 类型简单名}。

    过滤 @注解 token 与修饰词后：末 token 为变量名，其余拼回为类型文本；可变参 "Msg..." 去省略号。
    """
    out: dict[str, str] = {}
    for p in m.parameters:
        toks = [x for x in _GENERIC_RE.sub("", p).split() if not x.startswith("@")]
        if len(toks) < 2:
            continue
        tname = _simple_name(" ".join(toks[:-1]))
        if tname:
            out[toks[-1].rstrip("...")] = tname
    return out


def _infer_type(recv: str, m: CodeMethod, cls: CodeClass) -> str | None:
    """receiver 变量 → 声明类型简单名。优先级：方法参数 > 本类字段 > 局部变量；全 miss → None。"""
    for scope in (_param_types(m), cls.fields, m.local_types):
        t = scope.get(recv)
        if t:
            return t
    return None


def _descendants(name: str, children: dict[str, set[str]], limit: int = 5) -> set[str]:
    """类型名的全部后代类（沿 implements/extends 向下 BFS；防环，限深 limit）。

    起点预置进 seen——环（A↔B）把起点带回时不重复入集合，起点自身不算后代。
    """
    seen: set[str] = {name}
    frontier = [name]
    for _ in range(limit):
        nxt: list[str] = []
        for n in frontier:
            for ch in children.get(n, ()):
                if ch not in seen:
                    seen.add(ch)
                    nxt.append(ch)
        if not nxt:
            break
        frontier = nxt
    return seen - {name}


def build_call_graph(session: Session, repo_path: str | Path) -> dict:
    """从仓库源码重新解析调用表达式 → call_graph 边（M46：跨类四步解析）。

    calls 未落库，故此处重解析仓库；方法 → chunk_id 经 code_anchor_key 映射。
    四步：①receiver 定型（参数>字段>局部变量，miss 则 receiver 名当类名=静态调用）
    ②类型简单名匹配 class_name ③继承闭包分发到后代实现类 ④this/super/无 receiver 同类。
    """
    code_chunks = session.execute(
        select(CodeChunk).where(
            CodeChunk.is_deleted == False,  # noqa: E712
            CodeChunk.chunk_type == "method",
        )
    ).scalars().all()

    by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_key: dict[str, list[str]] = defaultdict(list)
    children: dict[str, set[str]] = defaultdict(set)  # 父类型简单名 → 子类名集合
    for c in code_chunks:
        if c.class_name and c.method_name:
            by_pair[(c.class_name, c.method_name)].append(c.chunk_id)
        if c.code_anchor_key:
            by_key[c.code_anchor_key].append(c.chunk_id)
        if c.extends_class:
            children[c.extends_class.rsplit(".", 1)[-1]].add(c.class_name)
        for iface in (c.implements_interface or "").split(","):
            if iface := iface.strip():
                children[iface.rsplit(".", 1)[-1]].add(c.class_name)

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
                    for recv, name in m.calls:
                        if recv in (None, "this", "super"):
                            target_ids = by_pair.get((cls.name, name), [])
                        else:
                            t = _infer_type(recv, m, cls)
                            if t is None:
                                t = recv  # fallback：receiver 名直接当类名（静态调用/同包直呼）
                            cands = {t} | _descendants(t, children)
                            target_ids = [cid for cn in cands for cid in by_pair.get((cn, name), [])]
                        for cid in target_ids:
                            edges.add((caller_id, cid, name, cid == caller_id))

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
