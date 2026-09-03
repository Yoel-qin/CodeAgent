"""Task 8：SpanCollector 纯件——嵌套/重复 end/未关闭丢弃/形状冻结。"""
from app.agent.trace import SpanCollector


def test_nested_spans_shape():
    c = SpanCollector()
    req = c.start("request", "chat")
    route = c.start("route", "query_analysis", parent_id=req)
    c.end(route, attrs={"intent": "code", "route": "codenav"})
    c.end(req)
    spans = c.to_dict()
    assert [s["kind"] for s in spans] == ["route", "request"]
    # 注：brief 原稿 unpacking 与其自身实现/冻结形状相反（spans[0]=route、spans[1]=request），
    # 按冻结形状修正指向——根 span parent_id=None，子 span parent_id=父 span_id。
    r, q = spans[0], spans[1]
    assert q["parent_id"] is None and r["parent_id"] == req
    assert set(q) == {"span_id", "parent_id", "kind", "name", "start_ms",
                      "duration_ms", "status", "error", "tokens", "attrs"}
    assert q["status"] == "ok" and q["error"] is None and q["tokens"] is None
    assert r["attrs"] == {"intent": "code", "route": "codenav"}


def test_error_and_tokens():
    c = SpanCollector()
    llm = c.start("llm", "llm")
    c.end(llm, tokens={"prompt": 10, "completion": 5, "estimated": False})
    tool = c.start("tool", "grep_code")
    c.end(tool, status="error", error="TimeoutError")
    s = {x["kind"]: x for x in c.to_dict()}
    assert s["llm"]["tokens"] == {"prompt": 10, "completion": 5, "estimated": False}
    assert s["tool"]["status"] == "error" and s["tool"]["error"] == "TimeoutError"


def test_double_end_and_unclosed_dropped():
    c = SpanCollector()
    a = c.start("tool", "t1")
    c.end(a)
    c.end(a)          # 重复 end 静默
    c.start("tool", "t2")   # 不 end
    assert [s["name"] for s in c.to_dict()] == ["t1"]


def test_durations_monotonic_nonneg():
    c = SpanCollector()
    a = c.start("request", "r")
    c.end(a)
    assert c.to_dict()[0]["duration_ms"] >= 0
    assert c.to_dict()[0]["start_ms"] >= 0
