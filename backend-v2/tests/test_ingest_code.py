"""Task 9: tree-sitter 解析移植 + code_entity 入库 + ingest CLI 测试。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.pipeline.ingest_code import entities_from_parsed, upsert_entities
from app.pipeline.parsing.code_parser import parse_java

FIX = Path(__file__).parent / "fixtures" / "mini_repo"


def test_parse_and_entities():
    """parse_java 解析 CommitLog.java → entities_from_parsed 产出类+方法实体 dict。"""
    src = (FIX / "com/example/broker/CommitLog.java").read_text(encoding="utf-8")
    pf = parse_java(src, "com/example/broker/CommitLog.java", module_name="com")
    cls = next(c for c in pf.classes if c.name == "CommitLog")
    m = next(m for m in cls.methods if m.name == "putMessage")
    assert m.source and "MAX_RETRY_TIMES" in m.source, "M46 修复语义：方法 source 含签名+体"
    assert ("flushService", "flush") in m.calls, "M46 调用对提取"

    rows = entities_from_parsed(pf, repo="mini", module="com")
    types = {(r["entity_type"], r["class_name"], r["method_name"]) for r in rows}
    assert ("class", "CommitLog", None) in types
    assert ("method", "CommitLog", "putMessage") in types
    assert all(r["repo"] == "mini" for r in rows)


def test_upsert_entities_idempotent_sqlite():
    """upsert_entities 二次调用 → inserted=0（sqlite 内存库验证幂等）。"""
    engine = create_engine("sqlite:///:memory:")
    from app.db.base import Base

    Base.metadata.create_all(engine)
    rows = [
        {
            "repo": "mini",
            "entity_type": "class",
            "class_name": "CommitLog",
            "method_name": None,
            "module": "com",
            "file_path": "com/example/broker/CommitLog.java",
            "start_line": 8,
            "end_line": 29,
            "signature": None,
        },
    ]
    with Session(engine) as s:
        r1 = upsert_entities(s, rows)
        assert r1["inserted"] == 1
        r2 = upsert_entities(s, rows)
        assert r2["inserted"] == 0
        assert r2["updated"] == 0


def test_upsert_entities_idempotent_pg(session):
    """upsert_entities 幂等性（PG 事务回滚 fixture）。"""
    rows = [
        {
            "repo": "mini",
            "entity_type": "method",
            "class_name": "CommitLog",
            "method_name": "putMessage",
            "module": "com",
            "file_path": "com/example/broker/CommitLog.java",
            "start_line": 18,
            "end_line": 28,
            "signature": "public boolean putMessage(String topic, byte[] body)",
        },
    ]
    r1 = upsert_entities(session, rows)
    assert r1["inserted"] == 1
    r2 = upsert_entities(session, rows)
    assert r2["inserted"] == 0
    assert r2["updated"] == 0
