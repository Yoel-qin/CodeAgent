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


async def test_ensure_tools_loaded_regressions(monkeypatch):
    """回归锁（终审 I-1）：``run_case`` 兜载工具的补丁不被误删/误改。

    现有 retrieve 降级测试把 ``load_tools`` 钉成 noop 且只断言降级路径——从
    ``run_case`` 删掉 ``await _ensure_tools_loaded()`` 它们照样绿（真实库验证
    run 278 的缺陷即此形态：独立进程无 lifespan → ``_TOOLS`` 恒空 → 全员
    降级 retrieve，CI 抓不住）。本测试经 ``run_case`` 真路径断言 ``load_tools``
    调用次数；``_TOOLS_ENSURED`` 全局经 monkeypatch 复位（无遗留 fixture 污染）。
    """
    from app.agent import codenav, nodes, query_analysis, react_base, tools_loader

    calls = {"n": 0}

    async def _counting_load(transports=None):
        calls["n"] += 1

    def _patch_retrieve_degrade():
        # 复用既有钉法：即便本机 8110 真在跑也不进真 ReAct/LLM，恒落 retrieve 兜底
        monkeypatch.setattr(query_analysis, "configured", lambda: False)
        monkeypatch.setattr(nodes, "configured", lambda: False)
        monkeypatch.setattr(react_base, "configured", lambda: False)
        monkeypatch.setattr(nodes, "grep_code",
                            lambda *a: {"matches": [], "total_count": 0,
                                        "truncated": False, "engine": "python"})
        monkeypatch.setattr(codenav, "get_code_tools", lambda *a, **kw: [])
        monkeypatch.setattr(nodes, "hybrid_search", lambda *a: {"results": []})

    case = golden.GoldenCase(id="c-ensure", query="CommitLog putMessage", repo="mini",
                             expect_code=["CommitLog.putMessage"])

    # 分支 A：独立进程形态（lifespan 未跑 → tools_ready False）——两条 case 只兜载一次
    monkeypatch.setattr(tools_loader, "tools_ready", lambda: False)
    monkeypatch.setattr(tools_loader, "load_tools", _counting_load)
    monkeypatch.setattr(harness, "_TOOLS_ENSURED", False)
    _patch_retrieve_degrade()
    await harness.run_case(case, EvalVariant())
    await harness.run_case(case, EvalVariant())
    assert calls["n"] == 1, f"tools_ready=False 时两条 case 应只触发一次 load_tools，实际 {calls['n']} 次"

    # 分支 B：backend 进程形态（lifespan 已载 → tools_ready True）——零次 load_tools（no-op）
    monkeypatch.setattr(tools_loader, "tools_ready", lambda: True)
    calls["n"] = 0
    monkeypatch.setattr(harness, "_TOOLS_ENSURED", False)
    await harness.run_case(case, EvalVariant())
    assert calls["n"] == 0, "tools_ready=True（lifespan 已载）时不应再触发 load_tools"


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
