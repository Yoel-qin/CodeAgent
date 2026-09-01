from pathlib import Path

import pytest

from app.core.graph_query import get_callees, get_callers, get_module_deps
from app.pipeline.call_graph import build_call_edges
from app.pipeline.ingest_code import entities_from_parsed, upsert_entities
from app.pipeline.ingest_edges import replace_edges
from app.pipeline.parsing.code_parser import parse_java

FIX = Path(__file__).parent / "fixtures" / "mini_repo"
REPO = "graph_test_tmp"


@pytest.fixture(scope="module")
def seeded(pg_engine):
    pfs = [parse_java(p.read_text(encoding="utf-8"), str(p.relative_to(FIX)))
           for p in sorted(FIX.rglob("*.java"))]
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(f"DELETE FROM code_entities WHERE repo = '{REPO}'")
    from sqlalchemy.orm import Session
    with Session(pg_engine) as s:
        for pf in pfs:
            upsert_entities(s, entities_from_parsed(pf, repo=REPO, module="com"))
        replace_edges(s, repo=REPO, edges=build_call_edges(pfs))
        s.commit()
    yield
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(f"DELETE FROM code_entities WHERE repo = '{REPO}'")


def test_callees_cross_class(seeded):
    res = get_callees(REPO, "CommitLog", "putMessage")
    assert any(e["callee_class"] == "FlushService" and e["callee_method"] == "flush"
               for e in res["edges"])


def test_callers_upstream(seeded):
    res = get_callers(REPO, "FlushService", "flush")
    assert any(e["caller_class"] == "CommitLog" for e in res["edges"])


def test_depth_limit(seeded):
    res = get_callees(REPO, "MessageConsumer", "consume", depth=1)
    assert all(e["depth"] <= 1 for e in res["edges"])


def test_module_deps_shape(seeded):
    res = get_module_deps(REPO, "example")
    assert "dependencies" in res
