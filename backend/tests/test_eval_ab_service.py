"""A/B 消融持久化单测（services/eval_run_service.run_ab_and_persist + helpers）。

无真实检索/DB：monkeypatch ``run_ab`` 返 canned ``ABReport``（``persist=False`` 不写库），
验证 COMPLETED 字段映射 / config.kind="ab" / aggregate 取 full 变体 / FAILED 翻转 / pair 解析 /
diagnose 裁剪 / graph_subset。
"""
from __future__ import annotations

import app.services.eval_run_service as svc
from app.eval.ab_service import ABReport


def _ab_report(*, with_diagnose: bool = True) -> ABReport:
    """canned ABReport：full/no_rerank 两变体 + 1 pair；aggregate 用 int key（验证 _normalize_agg）。"""
    per_query = [{"id": "a01", "text": "x", "recall": {"10": 1.0}, "rerank_on": True}]
    if with_diagnose:
        per_query[0]["recall_paths"] = {"vector": [{"chunk_id": "c1", "kind": "code"}]}
        per_query[0]["retrieved_kinds"] = ["code", "doc"]
    full_agg = {
        "n": 2,
        "recall": {1: 1.0, 3: 1.0, 5: 1.0, 10: 1.0},
        "precision": {1: 0.5, 10: 0.2},
        "mrr": 0.9,
        "ndcg": {10: 0.93},
    }
    no_agg = {**full_agg, "recall": {10: 0.1}, "mrr": 0.2, "ndcg": {10: 0.3}}
    return ABReport(
        config={"top_k": 10, "rewrite": "off", "n_queries": 2},
        variants={
            "full": {
                "ablation": {"vector": True, "lexical": True, "graph": True, "rerank": True, "crosslink": True},
                "desc": "全开",
                "aggregate": full_agg,
                "n_evaluable": 2,
                "n_queries": 2,
                "rerank_on_count": 2,
                "unresolved": 0,
                "per_query": list(per_query),
            },
            "no_rerank": {
                "ablation": {"vector": True, "lexical": True, "graph": True, "rerank": False, "crosslink": True},
                "desc": "关精排",
                "aggregate": no_agg,
                "n_evaluable": 2,
                "n_queries": 2,
                "rerank_on_count": 0,
                "unresolved": 0,
                "per_query": list(per_query),
            },
        },
        pairs=[{
            "name": "rerank", "claim": "精排 +15~25%",
            "baseline": "no_rerank", "treatment": "full",
            "metric_focus": ["precision", "ndcg", "mrr"],
            "delta": {"recall": {10: {"abs": 0.9, "pct": 900.0}}, "mrr": {"abs": 0.7, "pct": 350.0}},
        }],
    )


# ---- _resolve_pairs ----


def test_resolve_pairs_default_and_subset_and_unknown():
    assert len(svc._resolve_pairs(None)) == 4                      # M32：默认 4 组（加 crosslink）
    assert [p.name for p in svc._resolve_pairs(["rerank"])] == ["rerank"]
    try:
        svc._resolve_pairs(["bogus"])
        raise AssertionError("未知 pair 应抛 ValueError")
    except ValueError:
        pass


# ---- _trim_ab_report ----


def test_trim_ab_report_drops_diagnose_fields():
    report = _ab_report(with_diagnose=True).to_dict()
    assert "recall_paths" in report["variants"]["full"]["per_query"][0]
    trimmed = svc._trim_ab_report(report, diagnose=False)
    assert "recall_paths" not in trimmed["variants"]["full"]["per_query"][0]
    assert "retrieved_kinds" not in trimmed["variants"]["full"]["per_query"][0]
    # diagnose=True 原样保留
    full = svc._trim_ab_report(_ab_report(with_diagnose=True).to_dict(), diagnose=True)
    assert "recall_paths" in full["variants"]["full"]["per_query"][0]


# ---- run_ab_and_persist: COMPLETED（persist=False，无 DB）----


async def test_run_ab_and_persist_completed(monkeypatch):
    report = _ab_report()

    async def fake_run_ab(*a, **kw):
        return report

    monkeypatch.setattr(svc, "run_ab", fake_run_ab)
    run = await svc.run_ab_and_persist(None, pairs=["rerank"], persist=False)

    assert run.status == "COMPLETED"
    assert (run.config or {}).get("kind") == "ab"
    assert run.config["pairs"] == ["rerank"]
    # aggregate 取 full 变体且规整为字符串 key
    assert run.aggregate["recall"]["10"] == 1.0
    assert run.n_evaluable == 2 and run.rerank_on_count == 2
    # 完整 ABReport 存 config["report"]（含 variants/pairs）
    assert "report" in run.config
    assert run.config["report"]["pairs"][0]["name"] == "rerank"
    assert run.duration_ms is not None and run.duration_ms >= 0


# ---- run_ab_and_persist: FAILED（run_ab 抛错 → 翻 FAILED，不中断）----


async def test_run_ab_and_persist_failed(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("ab kaput")

    monkeypatch.setattr(svc, "run_ab", boom)
    run = await svc.run_ab_and_persist(None, persist=False)

    assert run.status == "FAILED"
    assert "RuntimeError" in (run.error_message or "")
    assert run.aggregate is None
    assert (run.config or {}).get("kind") == "ab"          # kind 仍标记


# ---- run_ab_and_persist: diagnose 裁剪 / 保留持久化的 per_query ----


async def test_run_ab_and_persist_diagnose_trim(monkeypatch):
    async def fake_run_ab(*a, **kw):
        return _ab_report(with_diagnose=True)

    monkeypatch.setattr(svc, "run_ab", fake_run_ab)
    run = await svc.run_ab_and_persist(None, pairs=["rerank"], diagnose=False, persist=False)
    pq = run.config["report"]["variants"]["full"]["per_query"][0]
    assert "recall_paths" not in pq and "retrieved_kinds" not in pq


async def test_run_ab_and_persist_diagnose_keep(monkeypatch):
    async def fake_run_ab(*a, **kw):
        return _ab_report(with_diagnose=True)

    monkeypatch.setattr(svc, "run_ab", fake_run_ab)
    run = await svc.run_ab_and_persist(None, pairs=["rerank"], diagnose=True, persist=False)
    pq = run.config["report"]["variants"]["full"]["per_query"][0]
    assert "recall_paths" in pq                               # diagnose=True 保留


# ---- run_ab_and_persist: tags 子集过滤透传 ----


async def test_run_ab_and_persist_tags_passed(monkeypatch):
    """tags 非空时过滤 queries 再传入 run_ab；空标签列表 → ValueError。"""
    from app.eval.eval_service import EvalQuery

    all_q = [
        EvalQuery(id="a01", text="x", relevant=[], tags=["rocketmq"]),
        EvalQuery(id="a02", text="y", relevant=[], tags=[]),
        EvalQuery(id="a03", text="z", relevant=[], tags=["rocketmq"]),
    ]
    received_queries: list = []

    async def fake_run_ab(session, queries, *, top_k, rewrite, pairs):
        received_queries.extend(queries)
        return _ab_report()

    monkeypatch.setattr(svc, "run_ab", fake_run_ab)
    monkeypatch.setattr(svc, "load_eval_queries", lambda path: list(all_q))

    run = await svc.run_ab_and_persist(None, pairs=["rerank"], tags=["rocketmq"], persist=False)
    assert run.status == "COMPLETED"
    assert len(received_queries) == 2  # only rocketmq-tagged queries
    assert run.config.get("tags") == ["rocketmq"]
    assert run.n_queries == 2


async def test_run_ab_and_persist_tags_empty_raises(monkeypatch):
    """tags 过滤后无 query → ValueError。"""
    from app.eval.eval_service import EvalQuery

    monkeypatch.setattr(
        svc, "load_eval_queries",
        lambda path: [EvalQuery(id="a01", text="x", relevant=[], tags=["other"])],
    )
    try:
        await svc.run_ab_and_persist(None, pairs=["rerank"], tags=["rocketmq"], persist=False)
        raise AssertionError("应抛 ValueError")
    except ValueError as e:
        assert "tags=" in str(e)


# ---- run_ab_and_persist: graph_subset 触发子集报告 ----


# ---- crosslink pair 退化告警 ----


async def test_crosslink_pair_warns_when_settings_off(caplog):
    """CROSSLINK_RECALL_ENABLED=off + crosslink pair → WARNING。"""
    import logging

    from app.eval.ab_service import EvalReport

    async def fake_run_eval(*a, **kw):
        return EvalReport(
            aggregate={"n": 1, "recall": {1: 0.5, 3: 0.5, 5: 0.5, 10: 0.5},
                      "precision": {1: 0.5, 10: 0.1}, "mrr": 0.5, "ndcg": {10: 0.5}},
            n_queries=1, n_evaluable=1, rerank_on_count=1, per_query=[], unresolved=[], config={},
        )

    import app.core.config as cfg_mod
    import app.eval.ab_service as ab_mod
    from app.eval.eval_service import EvalQuery
    orig = ab_mod.run_eval
    ab_mod.run_eval = fake_run_eval
    old_val = cfg_mod.settings.crosslink_recall_enabled
    cfg_mod.settings.crosslink_recall_enabled = False
    try:
        with caplog.at_level(logging.WARNING, logger="app.eval.ab_service"):
            await ab_mod.run_ab(
                None,
                [EvalQuery(id="a01", text="x", relevant=[])],
                pairs=[ab_mod.DEFAULT_PAIRS[3]],  # crosslink pair
            )
        assert any("crosslink_recall_enabled=off" in r.message for r in caplog.records)
    finally:
        ab_mod.run_eval = orig
        cfg_mod.settings.crosslink_recall_enabled = old_val


# ---- run_ab_and_persist: graph_subset 触发子集报告 ----


async def test_run_ab_and_persist_graph_subset(monkeypatch):
    calls: list[dict] = []

    async def fake_run_ab(session, queries, *, top_k, rewrite, pairs):
        calls.append({"n_q": len(queries), "pairs": [p.name for p in pairs]})
        return _ab_report()

    monkeypatch.setattr(svc, "run_ab", fake_run_ab)

    # 不带 call_chain tag 的评测集 → graph_subset 子集为空 → 不触发第二次 run_ab
    import app.services.eval_run_service as s
    monkeypatch.setattr(s, "load_eval_queries", lambda path: [])
    await svc.run_ab_and_persist(None, pairs=["graph"], graph_subset=True, persist=False)
    assert len(calls) == 1                                     # 子集空，未二次调用
