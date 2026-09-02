"""M5 离线管道——队列抽象（Plan 3 Task 11）。

webhook 入口（Task 12）与 worker（Task 13）都只面向 `PipeQueue`：enqueue 投递
ingest 事件，consume 拉取，处理成功 ack、失败 dead_letter（死信流留证）。

`RedisStreamQueue` 用 Redis Stream 的 consumer group 语义：XREADGROUP 只投给组内
一个消费者（可多 worker 并存）、未 ack 的消息留在 PEL（XPENDING 可见），重试由
调用方把 `attempts` 加一后重新 enqueue、超 `pipe_max_attempts` 落死信流。
`InMemoryQueue` 是同契约的内存实现（测试替身 / 本地零依赖跑通管道）。

约定（与 brief 决策一致）：
- 消息字段全 str（redis hash 字段）：kind 原样、payload JSON 编解码、attempts 十进制串。
- XGROUP CREATE 用 ``id="0"``（而非 ``"$"``）：组从流头消费历史消息——这样「先 enqueue
  再建组/consume」也读得到；首次之后的 BUSYGROUP 吞掉（组已存在即幂等）。
  建组时机是懒式（consume 前 ensure）而非仅 __init__：管理侧/测试常会 DEL 流 key，
  该操作连带删掉组，懒式建组让队列自愈，无需重建队列对象。
- depths 的 pending 用 ``xpending(stream, group)`` 摘要里的 count 字段
  （redis-py 返回 ``{"pending": n, "min": .., "max": .., "consumers": [..]}``）；
  组/流不存在等异常一律 pending=0 软失败。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import redis
from redis.exceptions import ResponseError

from app.core.config import settings


@dataclass
class PipeEvent:
    """一条管道事件。event_id 即 redis stream id（如 ``"1690000000000-0"``）。

    ``raw`` 是毒消息旁路留证：payload JSON 解码失败时存原始串（解码成功为 None），
    dead_letter 时原样落死信流的 ``payload_raw`` 字段，便于查案。
    """

    event_id: str
    kind: str  # push | file | graph_rebuild
    payload: dict[str, Any]
    attempts: int = 0
    raw: str | None = None


class PipeQueue(ABC):
    """离线管道队列抽象。Task 12/13 的 webhook 与 worker 只依赖此接口。"""

    @abstractmethod
    def enqueue(self, kind: str, payload: dict[str, Any], *, attempts: int = 0) -> str:
        """投递一条事件，返回 event_id。"""

    @abstractmethod
    def consume(self, *, count: int = 10, block_ms: int = 2000) -> list[PipeEvent]:
        """拉取至多 count 条；``block_ms<=0`` 表示非阻塞（无消息立即返回 []，不抛错）。"""

    @abstractmethod
    def ack(self, *events: PipeEvent) -> int:
        """确认处理完成，返回成功 ack 的条数。"""

    @abstractmethod
    def dead_letter(self, event: PipeEvent, error: str) -> str:
        """落死信（保留原 kind/payload/attempts + error），返回死信 id。"""

    @abstractmethod
    def depths(self) -> dict[str, int]:
        """``{"stream": 主流长度, "dead": 死信流长度, "pending": 未 ack 数}``。"""


class RedisStreamQueue(PipeQueue):
    """Redis Stream 实现（同步 redis-py；worker 里跑在线程中，不占事件循环）。"""

    def __init__(
        self,
        *,
        stream: str,
        dead: str,
        group: str,
        consumer: str = "w1",
        url: str | None = None,
    ) -> None:
        self.stream = stream
        self.dead = dead
        self.group = group
        self.consumer = consumer
        # decode_responses=True：id/字段值直接回 str，免去逐处 .decode()
        self.r = redis.Redis.from_url(
            url or settings.redis_url, decode_responses=True
        )
        self._ensure_group()

    # ---- 内部 ----

    def _ensure_group(self) -> None:
        """XGROUP CREATE id="0" MKSTREAM；BUSYGROUP（组已存在）吞掉，其余照抛。"""
        try:
            self.r.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    @staticmethod
    def _encode(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _decode_event(event_id: str, fields: dict[str, Any]) -> PipeEvent:
        raw_payload = fields.get("payload") or "{}"
        raw: str | None = None
        try:
            payload = json.loads(raw_payload)
        except (TypeError, ValueError):
            payload = {}  # 毒消息：消费不崩，原文留证到 raw（dead_letter 落 payload_raw）
            raw = raw_payload if isinstance(raw_payload, str) else str(raw_payload)
        try:
            attempts = int(fields.get("attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0
        return PipeEvent(
            event_id=event_id,
            kind=str(fields.get("kind") or ""),
            payload=payload,
            attempts=attempts,
            raw=raw,
        )

    @staticmethod
    def _iter_response(resp: Any) -> list[tuple[str, dict[str, Any]]]:
        """归一 XREADGROUP 返回（redis-py 实际回 list 形状；dict 形状也兼容）：
        外层 ``[[stream, [(id, {field: value}), ...]], ...]`` / ``{stream: [...]}``；
        内层 ``(id, fields)`` 列表 / ``{id: fields}``（RESP3 + 部分解析器组合的形状）
        两种都吃。
        """
        pairs: list[tuple[str, dict[str, Any]]] = []
        if not resp:
            return pairs
        entries = resp.items() if isinstance(resp, dict) else resp
        for _stream_name, stream_entries in entries:
            if isinstance(stream_entries, dict):  # 内层 dict 形状：{id: {field: value}}
                stream_entries = list(stream_entries.items())
            for event_id, fields in stream_entries or []:
                pairs.append((str(event_id), dict(fields or {})))
        return pairs

    # ---- PipeQueue ----

    def enqueue(self, kind: str, payload: dict[str, Any], *, attempts: int = 0) -> str:
        # XADD stream * kind <k> payload <json> attempts <n>
        return str(
            self.r.xadd(
                self.stream,
                {
                    "kind": kind,
                    "payload": self._encode(payload),
                    "attempts": str(attempts),
                },
            )
        )

    def consume(self, *, count: int = 10, block_ms: int = 2000) -> list[PipeEvent]:
        self._ensure_group()  # 流被 DEL 会连带删组，这里懒式重建（见模块 docstring）
        # XREADGROUP GROUP g c COUNT n BLOCK ms STREAMS s >（">" = 只取未投递过的）
        # block_ms<=0 → 不传 BLOCK（非阻塞轮询）。注意 Redis 的 BLOCK 0 语义是
        # 「无限阻塞」，绝非立即返回——省略参数才是非阻塞。
        resp = self.r.xreadgroup(
            self.group,
            self.consumer,
            streams={self.stream: ">"},
            count=count,
            block=block_ms if block_ms > 0 else None,
        )
        return [self._decode_event(eid, fields) for eid, fields in self._iter_response(resp)]

    def ack(self, *events: PipeEvent) -> int:
        if not events:
            return 0
        ids = [e.event_id for e in events]
        # XACK 返回成功确认的条数
        return int(self.r.xack(self.stream, self.group, *ids))

    def dead_letter(self, event: PipeEvent, error: str) -> str:
        # XADD dead * kind/payload/attempts/error——原样留证，外加错误信息；
        # 毒消息（payload 解码失败）再补 payload_raw：重编码的 payload 是空 dict，
        # 查案得看原始串
        fields: dict[str, str] = {
            "kind": event.kind,
            "payload": self._encode(event.payload),
            "attempts": str(event.attempts),
            "error": error,
        }
        if event.raw:
            fields["payload_raw"] = event.raw
        return str(self.r.xadd(self.dead, fields))

    def depths(self) -> dict[str, int]:
        pending = 0
        try:
            summary = self.r.xpending(self.stream, self.group)
            pending = int(summary.get("pending") or 0) if isinstance(summary, dict) else 0
        except Exception:  # noqa: BLE001 — 组/流不存在等一律软失败为 0
            pending = 0
        return {
            "stream": int(self.r.xlen(self.stream)),
            "dead": int(self.r.xlen(self.dead)),
            "pending": pending,
        }


class InMemoryQueue(PipeQueue):
    """同契约内存实现——测试替身 / 本地零依赖跑通管道。

    consume 无阻塞语义：直接弹出至多 count 条。pending = 已 consume 未 ack。
    """

    def __init__(self) -> None:
        self._items: list[PipeEvent] = []
        self._pending: dict[str, PipeEvent] = {}
        self._acked: set[str] = set()
        self.dead: list[PipeEvent] = []  # 与 RedisStreamQueue 的死信流对应
        self.dead_errors: dict[str, str] = {}
        self._seq = 0

    def enqueue(self, kind: str, payload: dict[str, Any], *, attempts: int = 0) -> str:
        self._seq += 1
        event_id = f"{self._seq}-0"
        self._items.append(
            PipeEvent(event_id=event_id, kind=kind, payload=dict(payload), attempts=attempts)
        )
        return event_id

    def consume(self, *, count: int = 10, block_ms: int = 2000) -> list[PipeEvent]:
        taken, self._items = self._items[:count], self._items[count:]
        for ev in taken:
            self._pending[ev.event_id] = ev
        return taken

    def ack(self, *events: PipeEvent) -> int:
        n = 0
        for ev in events:
            if self._pending.pop(ev.event_id, None) is not None:
                self._acked.add(ev.event_id)
                n += 1
        return n

    def dead_letter(self, event: PipeEvent, error: str) -> str:
        self.dead.append(event)
        self.dead_errors[event.event_id] = error
        return event.event_id

    def depths(self) -> dict[str, int]:
        return {
            "stream": len(self._items),
            "dead": len(self.dead),
            "pending": len(self._pending),
        }
