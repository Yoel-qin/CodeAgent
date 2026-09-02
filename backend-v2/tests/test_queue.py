"""M5 Task 11：队列抽象测试。真 Redis（compose 主栈）；测试流 key `v2:pipe:test-*`，
fixture 前后 DEL——删除 stream key 即连 consumer group 一起删掉。"""

import pytest

from app.pipeline.queue import InMemoryQueue, RedisStreamQueue


@pytest.fixture
def rq():
    q = RedisStreamQueue(stream="v2:pipe:test:events", dead="v2:pipe:test:dead",
                         group="test-g", consumer="c1")
    q.r.delete("v2:pipe:test:events", "v2:pipe:test:dead")
    yield q
    q.r.delete("v2:pipe:test:events", "v2:pipe:test:dead")


def test_redis_enqueue_consume_ack_roundtrip(rq):
    rq.enqueue("file", {"repo": "mini", "path": "A.java", "status": "M"})
    events = rq.consume(count=5, block_ms=500)
    assert len(events) == 1 and events[0].kind == "file"
    assert events[0].payload["path"] == "A.java"
    assert rq.ack(*events) == 1
    assert rq.consume(count=5, block_ms=300) == []


def test_dead_letter_and_depths(rq):
    rq.enqueue("file", {"repo": "mini", "path": "B.java"})
    ev = rq.consume(count=1, block_ms=500)[0]
    rq.dead_letter(ev, "boom")
    d = rq.depths()
    assert d["dead"] == 1 and d["stream"] >= 1


def test_attempts_roundtrip(rq):
    rq.enqueue("file", {"repo": "x"}, attempts=2)
    ev = rq.consume(count=1, block_ms=500)[0]
    assert ev.attempts == 2


def test_inmemory_queue_same_contract():
    q = InMemoryQueue()
    q.enqueue("push", {"repo": "m"})
    evs = q.consume(count=3)
    assert len(evs) == 1
    q.ack(*evs)
    assert q.depths()["pending"] == 0


# ---- Task 12 评审遗留三小修 ----


def test_consume_block_ms_zero_is_nonblocking(rq, monkeypatch):
    """block_ms<=0 必须不传 BLOCK：Redis 的 BLOCK 0 语义是「无限阻塞」，
    绝非立即返回——非阻塞轮询只能靠省略 BLOCK 参数。"""
    captured: dict = {}
    real = rq.r.xreadgroup

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(rq.r, "xreadgroup", spy)
    rq.enqueue("file", {"repo": "mini"})
    evs = rq.consume(count=1, block_ms=0)
    assert captured["block"] is None
    assert len(evs) == 1  # 非阻塞仍读得到已有消息


def test_poison_payload_raw_kept_to_dead_letter(rq):
    """毒消息（payload 非 JSON）消费不崩：原文留证 PipeEvent.raw，
    dead_letter 时原样落 payload_raw 字段（重编码的 payload 是空 dict，不足以查案）。"""
    rq.r.xadd(rq.stream, {"kind": "file", "payload": "{oops not json", "attempts": "1"})
    ev = rq.consume(count=1, block_ms=500)[0]
    assert ev.payload == {} and ev.raw == "{oops not json"
    rq.dead_letter(ev, "bad payload")
    dead = rq.r.xrange(rq.dead)[0][1]
    assert dead["payload_raw"] == "{oops not json"
    assert dead["kind"] == "file" and dead["error"] == "bad payload"


def test_iter_response_accepts_dict_entry_shape():
    """内层 dict 形状防御：部分 redis-py/RESP3 组合回 {id: {field: value}} 而非
    [(id, fields)]，归一器必须两种都吃。"""
    resp = {"v2:pipe:test:events": {"1-0": {"kind": "file", "payload": "{}", "attempts": "0"}}}
    pairs = RedisStreamQueue._iter_response(resp)
    assert pairs == [("1-0", {"kind": "file", "payload": "{}", "attempts": "0"})]
