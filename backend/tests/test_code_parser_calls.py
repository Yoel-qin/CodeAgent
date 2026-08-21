"""M46 Task1：code_parser 抽 (receiver, name) 调用对 + CodeClass.fields + CodeMethod.local_types。"""
from app.pipeline.parsing.code_parser import parse_java

SRC = """package demo;

import java.util.Map;

public class Producer {
    private final MessageStore store;
    private Map<String, SendResult> cache;
    protected org.apache.rocketmq.remoting.RemotingClient remote;

    public SendResult send(MessageQueue mq, final Msg msg) {
        RemotingClient client = factory.createClient();
        store.put(msg);
        client.invoke(msg);
        Validator.check(msg);
        this.retry(msg);
        super.toString();
        shutdown();
        return null;
    }

    void retry(Msg msg) {}
    void shutdown() {}
}
"""


def _cls(pf):
    return next(c for c in pf.classes if c.name == "Producer")


def test_fields_simple_and_generic():
    pf = parse_java(SRC, "Producer.java")
    cls = _cls(pf)
    assert cls.fields["store"] == "MessageStore"      # final 修饰剥掉
    assert cls.fields["cache"] == "Map"               # 泛型 <String, SendResult> 剥掉
    assert cls.fields["remote"] == "RemotingClient"   # 全限定声明取末段


def test_local_types():
    pf = parse_java(SRC, "Producer.java")
    m = next(m for m in _cls(pf).methods if m.name == "send")
    assert m.local_types == {"client": "RemotingClient"}


def test_calls_receiver_pairs():
    pf = parse_java(SRC, "Producer.java")
    m = next(m for m in _cls(pf).methods if m.name == "send")
    pairs = set(m.calls)
    assert ("store", "put") in pairs        # 字段调用
    assert ("client", "invoke") in pairs    # 局部变量调用
    assert ("Validator", "check") in pairs  # 类名直呼（静态）
    assert ("factory", "createClient") in pairs
    assert ("this", "retry") in pairs       # this. 前缀保留 receiver="this"
    assert ("super", "toString") in pairs   # super. 前缀保留 receiver="super"
    assert (None, "shutdown") in pairs      # 无 receiver
    assert all(isinstance(r, str) or r is None for r, _ in m.calls)


def test_calls_chained_receiver_is_none():
    src = "class A { void f() { getBuilder().build(); } }"
    pf = parse_java(src, "A.java")
    m = next(m for m in pf.classes[0].methods if m.name == "f")
    assert (None, "build") in set(m.calls)      # 链式：receiver 非 identifier → None（接受的漏边）
    assert (None, "getBuilder") in set(m.calls)  # 语句首调用无 receiver


def test_call_pairs_deduped_ordered():
    pf = parse_java(SRC, "Producer.java")
    m = next(m for m in _cls(pf).methods if m.name == "send")
    assert len(m.calls) == len(set(m.calls))  # 对级去重


def test_javadoc_method_source_includes_body():
    """M46 Task4 修复：带 javadoc 的方法 source 曾只取注释节点（丢签名+体）——
    重载若 javadoc 相同则 content 全同 → chunk_id 撞 PK（SimpleCharStream 构造器实锤）。"""
    src = """class A {
    /**
     * Constructor.
     */
    public A(int x) {
        this.x = x;
    }

    /**
     * Constructor.
     */
    public A(int x, int y) {
        this.x = x + y;
    }

    /** Start. */
    void begin() {
        start();
    }
}
"""
    pf = parse_java(src, "A.java")
    ctors = [m for m in pf.classes[0].methods if m.name == "A"]
    assert len(ctors) == 2
    assert "this.x = x;" in ctors[0].source and "public A(int x)" in ctors[0].source
    assert "int y" in ctors[1].signature and "this.x = x + y;" in ctors[1].source
    begin = next(m for m in pf.classes[0].methods if m.name == "begin")
    assert "start();" in begin.source and "Start." in begin.source


def test_chunk_dedup_and_signature_truncation():
    """M46 Task4 防御：同文件极端同 content 消歧后缀 + 超长签名截断到 512。"""
    from collections import Counter

    from app.pipeline.chunking.code_chunker import chunk_code_file
    from app.pipeline.parsing.code_parser import parse_java

    # 两个同名方法体完全一致（tree-sitter 不查语义，照抽）
    src = """class B {
    void go() { helper(); }
    void go() { helper(); }
    void go() { helper(); }
}
"""
    specs = chunk_code_file(parse_java(src, "B.java"), commit_hash="c", small_file_lines=0)
    ids = [s.chunk_id for s in specs]
    assert len(ids) == len(set(ids)), f"仍有重复: {[k for k, v in Counter(ids).items() if v > 1]}"
    assert any(i.endswith("_r1") for i in ids) and any(i.endswith("_r2") for i in ids)

    # 超长签名（>512）截断
    long_sig = f"void m({', '.join(f'int p{i}' for i in range(80))})"
    src2 = f"class C {{\n    {long_sig} {{}}\n}}\n"
    pf = parse_java(src2, "C.java")
    specs2 = chunk_code_file(pf, commit_hash="c", small_file_lines=0)
    method_specs = [s for s in specs2 if s.method_name == "m"]
    assert method_specs and len(method_specs[0].method_signature) <= 512
