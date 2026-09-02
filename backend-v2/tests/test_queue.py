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
