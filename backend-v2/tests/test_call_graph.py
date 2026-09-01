from pathlib import Path

from app.pipeline.call_graph import build_call_edges
from app.pipeline.parsing.code_parser import parse_java

FIX = Path(__file__).parent / "fixtures" / "mini_repo"


def _all_parsed():
    return [parse_java(p.read_text(encoding="utf-8"), str(p.relative_to(FIX)))
            for p in sorted(FIX.rglob("*.java"))]


def test_cross_class_direct_edge():
    edges = build_call_edges(_all_parsed())
    assert ("CommitLog", "putMessage", "FlushService", "flush") in {
        (e["caller_class"], e["caller_method"], e["callee_class"], e["callee_method"]) for e in edges
    }, "字段定型 flushService→FlushService 的跨类直调边"


def test_same_class_edge():
    edges = build_call_edges(_all_parsed())
    assert any(e["caller_class"] == e["callee_class"] == "MessageConsumer"
               and e["call_type"] == "same_class" for e in edges), "sleepQuietly 同类调用"
