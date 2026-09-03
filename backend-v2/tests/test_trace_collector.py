"""Task 8：SpanCollector 纯件——嵌套/重复 end/未关闭丢弃/形状冻结。

Task 9 修复轮补：parent_id 自动嵌套（start 缺省父 = 最内层未关 span）——
五个接线层都不传 parent_id（brief 冻结签名无此通道），树关系由采集器按
open 栈自动建立（TraceView 按 parent_id 渲树的前提）。
"""
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


# ── Task 9 修复轮：parent_id 自动嵌套 ─────────────────────────────────────


def test_auto_parent_nesting():
    """start 不传 parent_id → 缺省父 = 最内层未关 span（A→B→C：C 挂 B、B 挂 A）。"""
    c = SpanCollector()
    a = c.start("request", "chat")
    b = c.start("agent", "docqa")
    d = c.start("tool", "grep_code")
    c.end(d)
    e = c.start("llm", "llm")   # d 已关 → 缺省父仍是最内层 open 的 b
    c.end(e)
    c.end(b)
    c.end(a)
    s = {x["name"]: x for x in c.to_dict()}
    assert s["chat"]["parent_id"] is None
    assert s["docqa"]["parent_id"] == a
    assert s["grep_code"]["parent_id"] == b
    assert s["llm"]["parent_id"] == b


def test_explicit_parent_overrides_auto():
    """显式 parent_id 压过自动父（栈顶），且被显式指定后不改变栈行为。"""
    c = SpanCollector()
    a = c.start("request", "r")
    b = c.start("agent", "ag")
    explicit = c.start("tool", "t", parent_id=a)
    c.end(explicit)
    c.end(b)
    c.end(a)
    s = {x["name"]: x for x in c.to_dict()}
    assert s["t"]["parent_id"] == a


def test_ended_span_no_longer_parents_later_spans():
    """已 end 的 span 不再作为后续 start 的缺省父。"""
    c = SpanCollector()
    a = c.start("request", "r")
    b = c.start("route", "qa")
    c.end(b)
    after = c.start("agent", "ag")
    c.end(after)
    c.end(a)
    s = {x["name"]: x for x in c.to_dict()}
    assert s["ag"]["parent_id"] == a


def test_out_of_order_end_keeps_inner_parenting():
    """乱序 end（外层先关、内层仍 open）→ 栈按值退位，后续缺省父仍是内层。"""
    c = SpanCollector()
    a = c.start("request", "r")
    b = c.start("agent", "ag")
    c.end(a)                    # 乱序：外层先关
    x = c.start("tool", "t")    # 缺省父 = 仍 open 的 b
    c.end(x)
    c.end(b)
    s = {z["name"]: z for z in c.to_dict()}
    assert s["t"]["parent_id"] == b
    assert s["ag"]["parent_id"] == a


def test_root_parent_none_without_open_spans():
    """栈空时 start → 根 span（parent_id=None）；上一棵树关闭后新树仍为根。"""
    c = SpanCollector()
    r1 = c.start("request", "r1")
    c.end(r1)
    r2 = c.start("request", "r2")
    c.end(r2)
    assert all(x["parent_id"] is None for x in c.to_dict())
