from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.symbols import find_symbol
from app.pipeline.call_graph import build_call_edges
from app.pipeline.ingest_code import entities_from_parsed, upsert_entities
from app.pipeline.ingest_edges import replace_edges
from app.pipeline.parsing.code_parser import parse_java

FIX = "tests/fixtures"
FIX_PATH = Path(__file__).parent / "fixtures" / "mini_repo"
SYM_REPO = "sym_sql_tmp"


@pytest.fixture(scope="module")
def seeded_symbols(pg_engine):
    """mini fixture 实体+边入 PG（独立 repo 名，用后清理）。"""
    pfs = [parse_java(p.read_text(encoding="utf-8"), str(p.relative_to(FIX_PATH)))
           for p in sorted(FIX_PATH.rglob("*.java"))]
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(f"DELETE FROM code_entities WHERE repo = '{SYM_REPO}'")
    with Session(pg_engine) as s:
        for pf in pfs:
            upsert_entities(s, entities_from_parsed(pf, repo=SYM_REPO, module="com"))
        replace_edges(s, repo=SYM_REPO, edges=build_call_edges(pfs))
        s.commit()
    yield SYM_REPO
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(f"DELETE FROM code_entities WHERE repo = '{SYM_REPO}'")


def test_find_type_def():
    res = find_symbol(FIX, "mini_repo", "CommitLog")
    types = [loc for loc in res["locations"] if loc["kind"] == "type"]
    assert any(loc["file"].endswith("CommitLog.java") and "class CommitLog" in loc["content"] for loc in types)


def test_find_method_def():
    res = find_symbol(FIX, "mini_repo", "putMessage")
    methods = [loc for loc in res["locations"] if loc["kind"] == "method"]
    assert any(loc["file"].endswith("CommitLog.java") for loc in methods)


def test_find_ref_finds_usages():
    res = find_symbol(FIX, "mini_repo", "retryDelay", ref_type="ref")
    assert res["locations"]
    assert all(loc["kind"] == "ref" for loc in res["locations"])
    files = {loc["file"] for loc in res["locations"]}
    assert any(f.endswith("MessageConsumer.java") for f in files)


def test_empty_symbol_error():
    res = find_symbol(FIX, "mini_repo", "")
    assert "error" in res


def test_wrong_repo_def_returns_error():
    """def 路线：两条正则都因 repo 不存在而报错时，传播错误而非返回空结果。"""
    res = find_symbol(FIX, "nonexistent_repo", "CommitLog")
    assert "error" in res


def test_def_truncation_uses_total_count(monkeypatch):
    """def 路线：total_count 来自 grep_code 返回值，不受 matches 截断影响。"""
    fake_result = {
        "matches": [{"file": "X.java", "line": 1, "content": "class X {}"}] * 50,
        "total_count": 60,
        "truncated": True,
        "engine": "python",
    }
    call_count = 0
    def fake_grep(*_a, **_kw):
        nonlocal call_count
        call_count += 1
        return fake_result
    monkeypatch.setattr("app.core.symbols.grep_code", fake_grep)
    res = find_symbol("/tmp", "r", "SomeClass")
    assert res["total_count"] == 120  # 60 + 60 (type + method)
    assert res["truncated"] is True
    assert len(res["locations"]) == 50


def test_find_type_def_via_sql(seeded_symbols):
    """SQL 路径命中已入库的 CommitLog 类型实体。"""
    res = find_symbol(FIX, seeded_symbols, "CommitLog")
    types = [loc for loc in res["locations"] if loc["kind"] == "type"]
    assert any("CommitLog.java" in loc["file"] for loc in types)


def test_find_symbol_sql_falls_back_when_empty():
    """code_entity 空 repo → 回落正则路径（接口零变）。"""
    res = find_symbol(FIX, "mini_repo", "CommitLog")
    assert any(loc["kind"] == "type" for loc in res["locations"])
