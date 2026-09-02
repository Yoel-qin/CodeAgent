"""M5 worker 包（Task 12 起）——「队列事件 → 管道动作」的各个环节。

- ``a_files``：push 事件展开为 per-file 事件（含 .java 变更时追加 graph_rebuild）。
- :class:`WorkerError`：worker 侧可重试失败的统一异常——runner（Task 13）捕获后
  attempts+1 重投 / 超 ``pipe_max_attempts`` 落死信，进程不崩。
"""
from __future__ import annotations


class WorkerError(RuntimeError):
    """worker 处理失败（可重试）。runner 捕获走重试/死信，绝不向上崩。"""
