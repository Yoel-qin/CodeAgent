"""CLI：离线管道 worker——消费 Redis Stream 里的 push/file/graph_rebuild 事件。

Usage:
    uv run python scripts/pipe_worker.py --once                # 跑一轮退出
    uv run python scripts/pipe_worker.py --once --max-events 200
    uv run python scripts/pipe_worker.py --loop                # 常驻（2s 轮询）

--loop 下 Redis 断连（构造队列/消费抛 RedisError）→ 记日志睡 5s 重试，进程不退；
--once 下 Redis 不可用 → 记日志退出码 1。PG 会话由 runner 每事件独立管理
（一事件失败不连坐），本脚本只负责队列构造与轮调节奏。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# sys.path 自举（允许从 repo 根或 backend/ 运行）
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # pragma: no cover

import redis  # noqa: E402
from loguru import logger  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.pipeline.queue import RedisStreamQueue  # noqa: E402
from app.pipeline.runner import run_worker_once  # noqa: E402

_LOOP_INTERVAL_SECONDS = 2.0
_RECONNECT_SLEEP_SECONDS = 5.0


def main() -> None:
    parser = argparse.ArgumentParser(description="离线管道 worker（Redis Stream 消费者）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="跑一轮 run_worker_once 后退出")
    mode.add_argument("--loop", action="store_true", help="常驻循环（默认 2s 一轮）")
    parser.add_argument("--max-events", type=int, default=50, help="单轮最多消费事件数")
    args = parser.parse_args()

    while True:
        try:
            queue = RedisStreamQueue(
                stream=settings.pipe_stream,
                dead=settings.pipe_dead_stream,
                group=settings.pipe_group,
            )
            stats = run_worker_once(queue, max_events=args.max_events)
        except redis.exceptions.RedisError as exc:
            logger.warning("Redis 不可用（{}），{:.0f}s 后重试", exc, _RECONNECT_SLEEP_SECONDS)
            if args.once:
                sys.exit(1)
            time.sleep(_RECONNECT_SLEEP_SECONDS)
            continue
        logger.info("worker 轮次完成: {}", stats)
        if args.once:
            return
        time.sleep(_LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
