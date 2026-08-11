"""检索评测编排单测（eval/eval_service.py）。

无外部依赖：``_FakeSession`` 按 SQL 内容分发锚/类/字面解析，``recall_fn`` 注入假召回
（仿 tests/test_indexing.py 的 _FakeSession + monkeypatch 风格）。覆盖解析、聚合、跳过、DI 接缝。
"""
from __future__ import annotations

from app.eval.eval_service import EvalQuery, resolve_relevant, run_eval


class _Rows:
    """模拟 ``result.mappings().all()`` → list[dict]。"""

    def __init__(self, rows: list[dict]):
        self._rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """按 SQL 关键字分发：anchor / class / doc 字面 / code 字面。键集来自绑定参数。"""

    def __init__(self, *, anchors=None, classes=None, code_lits=None, doc_lits=None):
        self.anchors = anchors or {}     # {anchor: [chunk_id, ...]}
        self.classes = classes or {}     # {class: [chunk_id, ...]}
        self.code_lits = code_lits or set()
        self.doc_lits = doc_lits or set()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        keys = list((params or {}).values())[0] if params else []
        if "code_anchor_key" in sql:
            rows = [{"chunk_id": cid, "code_anchor_key": a} for a in keys for cid in self.anchors.get(a, [])]
        elif "class_name" in sql:
            rows = [{"chunk_id": cid, "class_name": c} for c in keys for cid in self.classes.get(c, [])]
        elif "doc_chunks" in sql:
            rows = [{"chunk_id": c} for c in keys if c in self.doc_lits]
        else:  # code_chunks 字面 chunk_id
            rows = [{"chunk_id": c} for c in keys if c in self.code_lits]
        return _Rows(rows)


# ---- resolve_relevant ----

async def test_resolve_relevant_anchor_class_literal_and_missing():
    session = _FakeSession(
        anchors={"Account.deposit": ["c_dep"]},
        classes={"Foo": ["c_foo"]},
        code_lits={"code_real"},
    )
    entries = ["Account.deposit", "Account.missing", "Foo", "code_real", "code_ghost"]
    resolved, missing = await resolve_relevant(session, entries)

    assert resolved == {"c_dep", "c_foo", "code_real"}
    assert set(missing) == {"Account.missing", "code_ghost"}


async def test_resolve_relevant_empty_entries():
    resolved, missing = await resolve_relevant(_FakeSession(), [])
    assert resolved == set() and missing == []


# ---- run_eval: 聚合 + rerank_on 计数 ----

async def test_run_eval_aggregates_and_counts_rerank():
    async def recall(session, query, *, top_k, **kw):
        if query == "存款":
            return [{"chunk_id": "c_dep"}, {"chunk_id": "x"}], {"rerank_on": True}
        return [{"chunk_id": "x"}, {"chunk_id": "c_wd"}], {"rerank_on": False}

    session = _FakeSession(anchors={"Account.deposit": ["c_dep"], "Account.withdraw": ["c_wd"]})
    queries = [
        EvalQuery("q1", "存款", ["Account.deposit"]),
        EvalQuery("q2", "取款", ["Account.withdraw"]),
    ]
    report = await run_eval(session, queries, top_k=10, rewrite="off", recall_fn=recall)

    assert report.n_queries == 2 and report.n_evaluable == 2
    assert report.rerank_on_count == 1
    # q1 首中(rank1) mrr=1.0；q2 次中(rank2) mrr=0.5 → 宏平均 0.75
    assert report.aggregate["mrr"] == round((1.0 + 0.5) / 2, 4)
    assert report.per_query[0]["rerank_on"] is True
    assert report.per_query[1]["rerank_on"] is False


# ---- run_eval: 未解析跳过 ----

async def test_run_eval_skips_unresolved():
    session = _FakeSession()  # 无任何锚/类 → 全部解析为空
    queries = [
        EvalQuery("q1", "A", ["Ghost.method"]),
        EvalQuery("q2", "B", ["Account.deposit"]),
    ]
    session.anchors = {"Account.deposit": ["c_dep"]}  # q2 可解析
    called = []

    async def recall(session, query, *, top_k, **kw):
        called.append(query)
        return [{"chunk_id": "c_dep"}], {"rerank_on": False}

    report = await run_eval(session, queries, recall_fn=recall)
    assert report.n_evaluable == 1            # 仅 q2
    assert len(report.unresolved) == 1 and report.unresolved[0]["id"] == "q1"
    assert called == ["B"]


# ---- run_eval: rewrite 模式 DI 接缝 ----

async def test_run_eval_off_mode_passes_deterministic_kwargs():
    seen: dict = {}

    async def recall(session, query, *, top_k, **kw):
        seen.update(kw)
        seen["top_k"] = top_k
        return [], {"rerank_on": False}

    session = _FakeSession(anchors={"Account.deposit": ["c_dep"]})
    await run_eval(session, [EvalQuery("q1", "如何 deposit", ["Account.deposit"])],
                   top_k=7, rewrite="off", recall_fn=recall)

    assert seen["semantic_query"] == "如何 deposit"   # 透传原 query 绕过 LLM 改写
    assert seen["rewritten"] is False
    assert seen["terms"]                              # 规则分词非空
    assert seen["top_k"] == 7


async def test_run_eval_auto_mode_omits_semantic_query():
    seen: dict = {}

    async def recall(session, query, *, top_k, **kw):
        seen.update(kw)
        return [], {"rerank_on": False}

    session = _FakeSession(anchors={"Account.deposit": ["c_dep"]})
    await run_eval(session, [EvalQuery("q1", "deposit", ["Account.deposit"])],
                   rewrite="auto", recall_fn=recall)

    assert "semantic_query" not in seen              # auto → 不传 → recall 内部走 LLM 改写


# ---- EvalReport.to_dict 可序列化 ----

async def test_report_to_dict_is_json_serializable():
    import json

    async def recall(session, query, *, top_k, **kw):
        return [{"chunk_id": "c_dep"}], {"rerank_on": True}

    session = _FakeSession(anchors={"Account.deposit": ["c_dep"]})
    report = await run_eval(session, [EvalQuery("q1", "deposit", ["Account.deposit"])], recall_fn=recall)
    d = report.to_dict()
    # 全是 JSON 原生类型
    json.dumps(d, ensure_ascii=False)
    assert d["config"] == {"top_k": 10, "rewrite": "off"}
    assert d["n_evaluable"] == 1


# ---- M25：per-query 诊断字段（retrieved_kinds / recall_paths）----

async def test_run_eval_records_kinds_and_recall_paths():
    async def recall(session, query, *, top_k, **kw):
        cands = [{"chunk_id": "c_dep", "kind": "code"}, {"chunk_id": "d1", "kind": "doc"}]
        meta = {"rerank_on": True,
                "recall_paths": {"vector": [{"chunk_id": "c_dep", "kind": "code"}]}}
        return cands, meta

    session = _FakeSession(anchors={"Account.deposit": ["c_dep"]})
    report = await run_eval(session, [EvalQuery("q1", "deposit", ["Account.deposit"])], recall_fn=recall)

    pq = report.per_query[0]
    assert pq["retrieved_kinds"] == ["code", "doc"]
    assert pq["recall_paths"]["vector"] == [{"chunk_id": "c_dep", "kind": "code"}]


async def test_run_eval_recall_paths_none_safe():
    """注入的 recall_fn 不发 recall_paths 时 per_query['recall_paths'] 为 None（向后兼容）。"""
    async def recall(session, query, *, top_k, **kw):
        return [{"chunk_id": "c_dep", "kind": "code"}], {"rerank_on": False}   # 无 recall_paths

    session = _FakeSession(anchors={"Account.deposit": ["c_dep"]})
    report = await run_eval(session, [EvalQuery("q1", "deposit", ["Account.deposit"])], recall_fn=recall)

    assert report.per_query[0]["recall_paths"] is None
    assert report.per_query[0]["retrieved_kinds"] == ["code"]
