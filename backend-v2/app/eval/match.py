"""citation ↔ golden 目标匹配（M8，纯函数）。

匹配规则（冻结，形状无关——grep/read_file/find_symbol/图边产出的 code citation 单行或
区间一律按「file_path 相等且 start_line 落在目标 [start,end] 闭区间」判命中）：
- code：``c.kind=="code" and c.file_path==t.file_path and t.start<=c.start_line<=t.end``
- doc：``c.kind=="doc" and c.doc_id==t.doc_name and c.section==t.anchor``（doc_id 即
  doc_name——tools_loader._doc 冻结契约）
"""
from __future__ import annotations

from app.eval.golden import CodeTarget, DocTarget


def code_hit(citation: dict, targets: list[CodeTarget]) -> bool:
    if citation.get("kind") != "code":
        return False
    line = citation.get("start_line")
    if not isinstance(line, int):
        return False
    fp = citation.get("file_path")
    return any(t.file_path == fp and t.start_line <= line <= max(t.end_line, t.start_line)
               for t in targets)


def doc_hit(citation: dict, targets: list[DocTarget]) -> bool:
    if citation.get("kind") != "doc":
        return False
    return any(t.doc_name == citation.get("doc_id") and t.anchor == citation.get("section")
               for t in targets)


def match_case(citations: list[dict], code_targets: list[CodeTarget],
               doc_targets: list[DocTarget]) -> dict:
    """一条 case 的全部 citation 对全部目标匹配 → 冻结四键结果。"""
    hits_code = any(code_hit(c, code_targets) for c in citations)
    hits_doc = any(doc_hit(c, doc_targets) for c in citations)
    matched = sum(1 for c in citations if code_hit(c, code_targets) or doc_hit(c, doc_targets))
    return {"hit_code": hits_code, "hit_doc": hits_doc,
            "matched": matched, "total": len(citations)}
