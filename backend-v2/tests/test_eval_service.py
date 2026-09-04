"""Task 6：评测服务——锚点解析（真 PG session fixture 回滚）+ run_and_persist
（monkeypatch harness.run_case 假证据；EvalRun 行真落真清）。

brief 适配（有据偏差，须带入评审）：
1. ``test_run_and_persist_ab`` 签名追加 ``_anchor_seed`` fixture——brief 断言
   ``code_hit_rate == 1.0``（注释「行区间 110∈[100,180]」即 seeded fixture 的实体行），
   但该测试未挂 ``seeded``，且 ``seeded`` 是回滚 session（``run_and_persist`` 经
   ``SessionLocal`` 新连接看不到回滚数据，任务书自述的既有模式）——无已提交锚点行时
   c1 也 unresolved、code_hit_rate=None。补一个真提交 + 测后按 repo 清除的锚点种子，
   断言语义（brief 注释明说的 110∈[100,180] 命中）不变。
2. ``_cleanup_runs`` teardown 追加 app engine ``dispose()``——本文件两测均经
   ``SessionLocal`` 用 app 共享 engine，pytest-asyncio 每测独立事件循环，池化
   asyncpg 连接跨测复用会炸（Event loop is closed，实测）；test_chat_api /
   test_eval_api 同款清池处理，断言零改动。
3. 其余逐字（含 CLI 解析段，经 importlib 载入 scripts/eval_run.py）。
"""
import pytest

from app.db.models import CodeEntity, DocSection, Document
from app.eval import golden
from app.services import eval_service


@pytest.fixture
def seeded(session):
    """锚点解析种子：1 方法实体 + 1 类实体 + 1 文档节（session 回滚不留痕）。"""
    session.add(CodeEntity(repo="test-eval", entity_type="method", class_name="CommitLog",
                           method_name="putMessage", module="store",
                           file_path="store/CommitLog.java", start_line=100, end_line=180))
    session.add(CodeEntity(repo="test-eval", entity_type="class", class_name="CommitLog",
                           method_name=None, module="store", file_path="store/CommitLog.java",
                           start_line=60, end_line=900))
    doc = Document(repo="test-eval", doc_name="设计.md", module=None, source_path="x",
                   doc_type="markdown", status="COMPLETED", file_hash="h")
    session.add(doc)
    session.flush()
    session.add(DocSection(document_id=doc.id, repo="test-eval", anchor="刷盘/同步",
                           title="同步刷盘", level=2, kind="text", content="c",
                           token_count=1, order_index=0))
    session.flush()
    return session


async def test_resolve_anchors(seeded):
    cases = [golden.GoldenCase(id="c1", query="q", repo="test-eval",
                               expect_code=["CommitLog.putMessage", "Ghost.m"],
                               expect_doc=["设计.md#刷盘/同步", "设计.md#无"])]
    out = await eval_service.resolve_anchors(seeded, cases)
    a = out["c1"]
    assert [t.start_line for t in a["code"]["CommitLog.putMessage"]] == [100]
    assert a["code"]["Ghost.m"] == [] and a["doc"]["设计.md#无"] == []
    assert len(a["doc"]["设计.md#刷盘/同步"]) == 1


def test_fix_repos_fills_empty():
    cases = [golden.GoldenCase(id="a", query="q", repo="", expect_code=["X"]),
             golden.GoldenCase(id="b", query="q", repo="keep", expect_code=["Y"])]
    fixed = eval_service.fix_repos(cases, "rocketmq")
    assert [c.repo for c in fixed] == ["rocketmq", "keep"]


# ── run_and_persist（假 harness 证据 + 真 PG 落账） ──────────────────────────


def _golden_file(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("""
repo: test-eval
cases:
  - id: c1
    query: "CommitLog putMessage 在哪"
    expect: {code: ["CommitLog.putMessage"]}
  - id: c2
    query: "另一个"
    expect: {code: ["Ghost.m"]}
""", encoding="utf-8")
    return str(p)


@pytest.fixture
def _cleanup_runs():
    from sqlalchemy import create_engine, text

    from app.core.config import settings
    eng = create_engine(settings.postgres_dsn_sync)
    with eng.connect() as conn:
        before = {r[0] for r in conn.execute(text("select id from eval_runs where repo='test-eval'"))}
    yield
    with eng.connect() as conn:
        conn.execute(text("delete from eval_runs where repo='test-eval' and id <> any(:ids)"),
                     {"ids": list(before)})
        conn.commit()
    eng.dispose()
    # brief 适配 3：app engine 池化 asyncpg 连接绑在本测事件循环——pytest-asyncio 每测
    # 新循环，跨测复用旧连接会在 close 处炸 RuntimeError: Event loop is closed
    # （实测；test_chat_api 的 TestClient 同款病同款药）——测后清池。
    import asyncio
    import logging

    from app.db.base import engine as app_engine
    slog = logging.getLogger("sqlalchemy")
    prev, slog.level = slog.level, logging.CRITICAL + 1
    try:
        asyncio.run(app_engine.dispose())
    finally:
        slog.setLevel(prev)


@pytest.fixture
def _anchor_seed():
    """已提交锚点行（run_and_persist 的锚点解析走 SessionLocal 新连接，回滚 session
    种子跨连接不可见）；先清后插防残留 UK 冲突，测后按 repo 清除。"""
    from sqlalchemy import create_engine, text

    from app.core.config import settings
    eng = create_engine(settings.postgres_dsn_sync)
    with eng.connect() as conn:
        conn.execute(text("delete from code_entities where repo='test-eval'"))
        conn.execute(text(
            "insert into code_entities (repo, entity_type, class_name, method_name, module, "
            "file_path, start_line, end_line) values ('test-eval', 'method', 'CommitLog', "
            "'putMessage', 'store', 'store/CommitLog.java', 100, 180)"))
        conn.commit()
    yield
    with eng.connect() as conn:
        conn.execute(text("delete from code_entities where repo='test-eval'"))
        conn.commit()
    eng.dispose()


async def test_run_and_persist_ab(monkeypatch, tmp_path, _cleanup_runs, _anchor_seed):
    from app.eval.harness import CaseEvidence

    async def _fake_run(case, variant):
        cits = ([{"kind": "code", "file_path": "store/CommitLog.java",
                  "start_line": 110, "end_line": 110, "label": "x"}]
                if case.id == "c1" else [])
        return CaseEvidence(case_id=case.id, variant=variant.name, answer="答" * 40,
                            citations=cits, agent_steps=[{"tool": "t"}] * 2,
                            route="codenav", duration_ms=50.0,
                            token_usage={"spent_tokens": 42, "llm_calls": 1})

    async def _fake_judge(q, a, c):
        return {"faithfulness": 1.0, "answer_relevance": 1.0,
                "citation_accuracy": 1.0, "hallucination": 0.0}

    monkeypatch.setattr(eval_service, "run_case", _fake_run)
    monkeypatch.setattr(eval_service, "judge_case", _fake_judge)

    result = await eval_service.run_and_persist(
        repo=None, variants=[{"name": "baseline"}, {"name": "r4", "rounds_code": 4}],
        judge=True, golden_path=_golden_file(tmp_path), trigger="test")
    assert result["status"] == "DONE" and result["kind"] == "ab"
    m = result["metrics"]
    # baseline：c1 命中（行区间 110∈[100,180]）+ c2 无锚点目标（unresolved 分母剔除）
    assert m["variants"]["baseline"]["code_hit_rate"] == 1.0
    assert m["variants"]["r4"]["code_hit_rate"] == 1.0
    assert m["judge"]["faithfulness"] == 1.0
    rows = result["per_query"]
    assert len(rows) == 4  # 2 变体 × 2 case
    c2 = next(r for r in rows if r["case_id"] == "c2" and r["variant"] == "baseline")
    assert c2["unresolved"] == ["Ghost.m"] and c2["has_code_anchor"] is False
    listed = await eval_service.list_runs(limit=10)
    assert any(i["id"] == result["id"] for i in listed["items"])
    detail = await eval_service.get_run(result["id"])
    assert detail is not None and detail["per_query"]


async def test_run_and_persist_failed_and_no_persist(monkeypatch, tmp_path, _cleanup_runs):
    async def _boom(case, variant):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(eval_service, "run_case", _boom)
    result = await eval_service.run_and_persist(
        variants=[{}], golden_path=_golden_file(tmp_path), trigger="test")
    assert result["status"] == "FAILED" and "kaboom" in result["error"]

    async def _ok(case, variant):
        from app.eval.harness import CaseEvidence
        return CaseEvidence(case_id=case.id, variant=variant.name)

    monkeypatch.setattr(eval_service, "run_case", _ok)
    preview = await eval_service.run_and_persist(
        variants=[{}], golden_path=_golden_file(tmp_path), persist=False, trigger="test")
    assert preview["id"] is None and preview["metrics"]["variants"]["baseline"]["n_cases"] == 2


async def test_run_and_persist_bad_golden_path(monkeypatch, tmp_path, _cleanup_runs):
    """I1：golden 加载失败（路径不存在）不抛——落一行 FAILED（repo 回落 settings 默认）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "default_repo", "test-eval")  # 回落值钉到可清理 repo
    result = await eval_service.run_and_persist(
        golden_path=str(tmp_path / "no-such.yaml"), trigger="test")
    assert result["status"] == "FAILED" and result["error"]
    assert result["repo"] == "test-eval" and result["id"] is not None
    detail = await eval_service.get_run(result["id"])  # 新 SessionLocal 复核库里确有
    assert detail is not None and detail["status"] == "FAILED" and detail["error"]


def _cli():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "eval_run_cli", Path(__file__).resolve().parent.parent / "scripts" / "eval_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_variant_arg():
    cli = _cli()
    assert cli.parse_variant_arg("r4:rounds_code=4,rounds_doc=2") == {
        "name": "r4", "rounds_code": 4, "rounds_doc": 2}
    assert cli.parse_variant_arg("nograph:code_no_graph=1") == {
        "name": "nograph", "code_no_graph": True}
    assert cli.parse_variant_arg("m2:model_reasoning=qwen2.5-7b") == {
        "name": "m2", "model_reasoning": "qwen2.5-7b"}
    assert cli.parse_variant_arg("baseline") == {"name": "baseline"}
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_variant_arg(":rounds_code=4")


def test_cli_duplicate_ab_names_exit_2():
    """--ab 同名变体在报告 agg 按名分组会静默合并——CLI 对齐 API 侧 422，报错退出 2。"""
    import argparse
    import asyncio

    cli = _cli()
    args = argparse.Namespace(
        ab=["r4:rounds_code=4", "r4:code_no_graph=1"],
        repo="rocketmq", judge=False, set=None, no_persist=True)
    assert asyncio.run(cli._run(args)) == 2
