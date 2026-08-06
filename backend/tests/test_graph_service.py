"""知识图谱纯函数单测（services/graph_service.py 的 helper，无 DB/无 infra）。"""
from __future__ import annotations

from app.services.graph_service import (
    aggregate_call_edges,
    group_module_edges,
    normalize_doc_edge,
)

# ---- aggregate_call_edges ----

def test_aggregate_counts_duplicates_as_weight():
    weights = aggregate_call_edges([("a", "b"), ("a", "b"), ("b", "c")])
    assert weights == {("a", "b"): 2, ("b", "c"): 1}


def test_aggregate_empty():
    assert aggregate_call_edges([]) == {}


# ---- group_module_edges ----

def test_group_skips_self_loops_and_aggregates_cross_edges():
    # modA: c1(clsA)→c2(modB/clsB)；modA 内部 c3(clsA2)→c4(clsA) 自环
    call_edges = [("c1", "c2"), ("c3", "c4"), ("c1", "c3")]
    group_of = {"c1": "modA", "c2": "modB", "c3": "modA", "c4": "modA"}
    class_of = {"c1": "clsA", "c2": "clsB", "c3": "clsA2", "c4": "clsA"}
    node_classes, weights = group_module_edges(call_edges, group_of, class_of)
    # 跨组边只有 modA→modB（1 条）；两条自环跳过
    assert weights == {("modA", "modB"): 1}
    # modA 含 clsA/clsA2；modB 含 clsB
    assert node_classes["modA"] == {"clsA", "clsA2"}
    assert node_classes["modB"] == {"clsB"}
    # 仅出现在自环里的组仍是节点
    assert "modA" in node_classes


def test_group_skips_edges_with_unknown_group():
    call_edges = [("c1", "cX")]
    group_of = {"c1": "modA"}  # cX 缺失
    class_of = {"c1": "clsA"}
    node_classes, weights = group_module_edges(call_edges, group_of, class_of)
    assert weights == {}
    assert node_classes["modA"] == {"clsA"}


# ---- normalize_doc_edge ----

def test_normalize_doc_to_code_swaps_to_code_doc():
    # DOC_TO_CODE：source=doc, target=code → 规整为 (code, doc)
    assert normalize_doc_edge("doc1", "code1", "DOC_TO_CODE", False, None) == ("code1", "doc1", False, None)


def test_normalize_code_to_doc_keeps_order():
    # CODE_TO_DOC：source=code, target=doc → 已是 (code, doc)
    assert normalize_doc_edge("code1", "doc1", "CODE_TO_DOC", False, None) == ("code1", "doc1", False, None)


def test_normalize_passes_stale_through():
    assert normalize_doc_edge("d", "c", "DOC_TO_CODE", True, "代码已改文档未更") == (
        "c", "d", True, "代码已改文档未更")
