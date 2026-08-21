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
