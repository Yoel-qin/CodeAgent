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


def test_module_deps_aggregation(pg_engine):
    """跨模块聚合行为契约：call_count 累加 / top3 key_classes 按类级 cnt DESC / 模块按总调用数 DESC。

    作为 N+1 → 单查询聚合重构的回归锁（行为不变，性能由 bench 验证）。
    mini_repo fixture 全部实体同 module 造不出跨模块数据，故 ORM 直插。
    """
    from sqlalchemy.orm import Session

    from app.db.models.code_graph import CallEdge, CodeEntity

    repo = "graph_deps_tmp"
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(f"DELETE FROM code_entities WHERE repo = '{repo}'")

    def _ent(cls, meth, module, line):
        return CodeEntity(repo=repo, entity_type="method", class_name=cls,
                          method_name=meth, module=module, file_path=f"{cls}.java",
                          start_line=line)

    ents = [
        _ent("A", "run", "mod.a", 10),      # caller
        _ent("B1", "x", "mod.b", 1),        # 被 A 调 3 次
        _ent("B2", "y", "mod.b", 1),        # 被 A 调 2 次
        _ent("B3", "z", "mod.b", 1),        # 被 A 调 1 次
        _ent("C1", "q", "mod.c", 1),        # 被 A 调 3 次
    ]
    with Session(pg_engine) as s:
        s.add_all(ents)
        s.flush()
        line = 100
        for callee, n in [(ents[1], 3), (ents[2], 2), (ents[3], 1), (ents[4], 3)]:
            for _ in range(n):
                s.add(CallEdge(caller_id=ents[0].id, callee_id=callee.id,
                               call_type="call", call_site_file="A.java",
                               call_site_line=line))
                line += 1
        s.commit()

        try:
            res = get_module_deps(repo, "mod.a")
            assert [d["module"] for d in res["dependencies"]] == ["mod.b", "mod.c"]
            b = res["dependencies"][0]
            assert b["call_count"] == 6
            assert b["key_classes"] == ["B1", "B2", "B3"]
            c = res["dependencies"][1]
            assert c["call_count"] == 3
            assert c["key_classes"] == ["C1"]
        finally:
            s.connection().exec_driver_sql(f"DELETE FROM code_entities WHERE repo = '{repo}'")
            s.commit()
