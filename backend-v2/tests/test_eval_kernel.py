"""Task 2：评测纯函数内核——golden 加载/锚点解析/匹配/聚合，全离线零 IO（除 tmp YAML）。"""
import pytest

from app.eval import golden, match, metrics

# ── golden.load_golden_set ────────────────────────────────────────────────

def _write(tmp_path, body: str) -> str:
    p = tmp_path / "golden.yaml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_load_golden_set_inherits_file_level_repo(tmp_path):
    path = _write(tmp_path, """
repo: rocketmq
cases:
  - id: c1
    query: "CommitLog 在哪"
    expect: {code: ["CommitLog.putMessage"]}
  - id: c2
    query: "文档怎么说"
    repo: mini
    expect: {doc: ["a.md#sec-1"]}
""")
    repo, cases = golden.load_golden_set(path)
    assert repo == "rocketmq" and len(cases) == 2
    assert cases[0].repo == "rocketmq" and cases[1].repo == "mini"
    assert cases[0].expect_code == ["CommitLog.putMessage"] and cases[0].expect_doc == []
    assert cases[1].expect_doc == ["a.md#sec-1"] and cases[1].expect_code == []


@pytest.mark.parametrize("body, msg", [
    ("cases: []\nrepo: r\n", "无 case"),  # cases 空列表
    ("repo: r\ncases:\n  - id: c1\n    query: q\n    expect: {}\n", "expect 两列表全空"),
    ("repo: r\ncases:\n  - id: c1\n    query: q\n    expect: {code: [\"A\"]}\n"
     "  - id: c1\n    query: q2\n    expect: {code: [\"B\"]}\n", "id 重复"),
])
def test_load_golden_set_rejects_bad_sets(tmp_path, body, msg):
    with pytest.raises(ValueError):
        golden.load_golden_set(_write(tmp_path, body))


# ── 锚点解析（纯函数：rows 进、targets 出） ────────────────────────────────

_CROWS = [
    {"class_name": "CommitLog", "method_name": "putMessage", "file_path": "store/CommitLog.java",
     "start_line": 100, "end_line": 180},
    {"class_name": "CommitLog", "method_name": "putMessage", "file_path": "store/CommitLog.java",
     "start_line": 200, "end_line": 260},  # 重载：同名多实体 = 多可接受目标
    {"class_name": "CommitLog", "method_name": None, "file_path": "store/CommitLog.java",
     "start_line": 60, "end_line": 900},
    {"class_name": "Other", "method_name": "putMessage", "file_path": "o.java",
     "start_line": 1, "end_line": 2},
]


def test_parse_code_spec():
    assert golden.parse_code_spec("CommitLog.putMessage") == ("CommitLog", "putMessage")
    assert golden.parse_code_spec("CommitLog") == ("CommitLog", None)


def test_resolve_code_targets_method_and_class():
    ts = golden.resolve_code_targets(_CROWS, "CommitLog.putMessage")
    assert [(t.start_line, t.end_line) for t in ts] == [(100, 180), (200, 260)]
    assert all(t.file_path == "store/CommitLog.java" for t in ts)
    cls = golden.resolve_code_targets(_CROWS, "CommitLog")
    assert len(cls) == 1 and (cls[0].start_line, cls[0].end_line) == (60, 900)


def test_resolve_code_targets_end_line_missing_falls_back():
    rows = [{"class_name": "A", "method_name": "m", "file_path": "a.java", "start_line": 5,
             "end_line": None}]
    assert golden.resolve_code_targets(rows, "A.m") == [golden.CodeTarget("a.java", 5, 5)]


def test_resolve_code_targets_no_match_returns_empty():
    assert golden.resolve_code_targets(_CROWS, "NoSuch.m") == []


def test_resolve_doc_targets():
    rows = [{"doc_name": "存储设计.md", "anchor": "刷盘/同步刷盘"},
            {"doc_name": "其他.md", "anchor": "x"}]
    ts = golden.resolve_doc_targets(rows, "存储设计.md#刷盘/同步刷盘")
    assert ts == [golden.DocTarget("存储设计.md", "刷盘/同步刷盘")]
    assert golden.resolve_doc_targets(rows, "存储设计.md#不存在") == []


# ── 匹配 ──────────────────────────────────────────────────────────────────

_CTS = [golden.CodeTarget("store/CommitLog.java", 100, 180)]
_DTS = [golden.DocTarget("存储设计.md", "刷盘/同步刷盘")]


def test_match_case_hits():
    cits = [
        {"kind": "code", "file_path": "store/CommitLog.java", "start_line": 120, "end_line": 120},
        {"kind": "doc", "doc_id": "存储设计.md", "section": "刷盘/同步刷盘"},
        {"kind": "code", "file_path": "other.java", "start_line": 1, "end_line": 1},  # 不匹配
    ]
    m = match.match_case(cits, _CTS, _DTS)
    assert m == {"hit_code": True, "hit_doc": True, "matched": 2, "total": 3}


def test_match_case_boundaries():
    # 行号恰在区间端点命中；出区间/文件不符/kind 不符不命中
    assert match.match_case([{"kind": "code", "file_path": "store/CommitLog.java",
                              "start_line": 180, "end_line": 180}], _CTS, [])["hit_code"] is True
    assert match.match_case([{"kind": "code", "file_path": "store/CommitLog.java",
                              "start_line": 181, "end_line": 181}], _CTS, [])["hit_code"] is False
    assert match.match_case([{"kind": "doc", "doc_id": "存储设计.md",
                              "section": "别的"}], [], _DTS)["hit_doc"] is False
    assert match.match_case([], _CTS, _DTS) == {"hit_code": False, "hit_doc": False,
                                                "matched": 0, "total": 0}


# ── 聚合 ──────────────────────────────────────────────────────────────────

def _row(**kw):
    base = {"case_id": "c", "variant": "baseline", "hit_code": False, "hit_doc": False,
            "has_code_anchor": True, "has_doc_anchor": False, "matched": 0, "total": 0,
            "precision": None, "rounds": 0, "latency_ms": 0.0, "tokens": 0,
            "llm_calls": 0, "route": "retrieve", "answer_chars": 0, "unresolved": []}
    base.update(kw)
    return base


def test_aggregate_full():
    agg = metrics.aggregate([
        _row(hit_code=True, total=2, matched=1, precision=0.5, rounds=3, latency_ms=100.0, tokens=50),
        _row(hit_code=False, total=0, matched=0, precision=None, rounds=1, latency_ms=300.0, tokens=150),
    ])
    assert agg["n_cases"] == 2
    assert agg["code_hit_rate"] == 0.5          # 分母只数 has_code_anchor 的行
    assert agg["doc_hit_rate"] is None          # 无 has_doc_anchor 行 → None（不是 0）
    assert agg["citation_precision"] == 0.5     # 分母只数 total>0 的行
    assert agg["rounds_mean"] == 2.0 and agg["rounds_p95"] == 3.0
    assert agg["latency_p50_ms"] == 100.0 and agg["latency_p95_ms"] == 300.0
    assert agg["tokens_mean"] == 100.0


def test_aggregate_empty_and_unresolved_excluded():
    assert metrics.aggregate([])["n_cases"] == 0
    assert metrics.aggregate([])["code_hit_rate"] is None
    # unresolved 锚点行不进分母：has_code_anchor=False 的行不拖低命中率
    agg = metrics.aggregate([_row(hit_code=False, has_code_anchor=False)])
    assert agg["code_hit_rate"] is None


def test_percentile():
    assert metrics.percentile([], 0.5) is None
    assert metrics.percentile([1.0], 0.95) == 1.0
    assert metrics.percentile([1, 2, 3, 4], 0.5) == 2.0
    assert metrics.percentile([1, 2, 3, 4], 0.95) == 4.0
