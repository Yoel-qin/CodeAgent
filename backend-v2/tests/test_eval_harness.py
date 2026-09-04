"""Task 4：harness——monkeypatch 面 = 图节点的模块属性（同 test_chat_api 模式）；
不落业务表（本文件不触 PG；落表断言由 Task 6 service 测试覆盖）。

brief 适配（有据偏差，须带入评审）：brief 测试头的 ``import json`` 全文件未使用，
ruff F401 会打红硬门（``uv run ruff check .`` 净）——删除该行，其余逐字。
"""
from app.eval import golden, harness
from app.eval.harness import EvalVariant


def test_build_config_defaults_and_overrides():
    from app.agent.cost import CostController

    cost = CostController(max_tokens=10, max_llm_calls=2)
    cfg = harness._build_config(EvalVariant(), cost)
    assert cfg["configurable"]["cost"] is cost
    assert cfg["configurable"]["top_k"] == 8 and "rounds_code" not in cfg["configurable"]
    assert cfg["recursion_limit"] == 60

    v = EvalVariant(name="r4", rounds_code=4, rounds_doc=2, code_no_graph=True, top_k=3)
    cfg2 = harness._build_config(v, cost)
    assert cfg2["configurable"]["rounds_code"] == 4
    assert cfg2["configurable"]["rounds_doc"] == 2
    assert cfg2["configurable"]["code_no_graph"] is True
    assert cfg2["configurable"]["top_k"] == 3


def test_variant_model_overrides():
    assert EvalVariant(model_reasoning="m-x").model_overrides() == {"reasoning": {"model": "m-x"}}
    assert EvalVariant().model_overrides() == {}
    assert EvalVariant(code_no_graph=True).overrides() == {"code_no_graph": True}


async def test_run_case_collects_evidence_no_persist(monkeypatch):
    """retrieve 路（无 LLM/无工具）：事件证据收齐 + 不抛 + token_usage 键齐。

    规则分类判 code → codenav——钉 ``codenav.get_code_tools`` 为空 + react_base
    ``configured`` 为 False，确保即便本机 8110 真在跑（此前测试的 lifespan 已把工具
    载入 ``_TOOLS``）也不会进真 ReAct/LLM：空工具分支先行降级 retrieve。
    """
    from app.agent import codenav, nodes, query_analysis, react_base, tools_loader

    async def _noop_load(transports=None):
        return None

    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    monkeypatch.setattr(query_analysis, "configured", lambda: False)
    monkeypatch.setattr(nodes, "configured", lambda: False)
    monkeypatch.setattr(react_base, "configured", lambda: False)
    monkeypatch.setattr(nodes, "grep_code",
                        lambda *a: {"matches": [{"file": "a/CommitLog.java", "line": 10,
                                                 "content": "putMessage"}],
                                    "total_count": 1, "truncated": False, "engine": "python"})
    monkeypatch.setattr(codenav, "get_code_tools", lambda *a, **kw: [])
    monkeypatch.setattr(nodes, "hybrid_search", lambda *a: {"results": []})

    case = golden.GoldenCase(id="c1", query="CommitLog putMessage", repo="mini",
                             expect_code=["CommitLog.putMessage"])
    ev = await harness.run_case(case, EvalVariant())
    assert ev.case_id == "c1" and ev.variant == "baseline"
    assert ev.route == "retrieve"
    assert len(ev.citations) >= 1 and ev.citations[0]["kind"] == "code"
    assert ev.answer  # 无 key 片段摘要
    assert ev.duration_ms > 0
    assert set(ev.token_usage) >= {"spent_tokens", "llm_calls", "estimated"}


def test_build_row_full_shape():
    case = golden.GoldenCase(id="c1", query="q", repo="mini",
                             expect_code=["CommitLog.putMessage", "Ghost.m"],
                             expect_doc=["a.md#sec-1"])
    ev = harness.CaseEvidence(case_id="c1", variant="v1", answer="x" * 30,
                              citations=[{"kind": "code", "file_path": "store/CommitLog.java",
                                          "start_line": 120, "end_line": 120},
                                         {"kind": "code", "file_path": "o.java",
                                          "start_line": 1, "end_line": 1}],
                              agent_steps=[{"tool": "t"}] * 3, route="codenav",
                              duration_ms=123.4,
                              token_usage={"spent_tokens": 99, "llm_calls": 2})
    row = harness.build_row(
        case, ev,
        code_targets={"CommitLog.putMessage": [golden.CodeTarget("store/CommitLog.java", 100, 180)]},
        doc_targets={},
    )
    assert row["case_id"] == "c1" and row["variant"] == "v1"
    assert row["hit_code"] is True and row["hit_doc"] is False
    assert row["has_code_anchor"] is True and row["has_doc_anchor"] is False
    assert row["matched"] == 1 and row["total"] == 2 and row["precision"] == 0.5
    assert row["rounds"] == 3 and row["latency_ms"] == 123.4
    assert row["tokens"] == 99 and row["llm_calls"] == 2
    assert row["route"] == "codenav" and row["answer_chars"] == 30
    assert row["unresolved"] == ["Ghost.m", "a.md#sec-1"]
