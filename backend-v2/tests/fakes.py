"""测试替身集（M5 起）。Task 12/13 的测试统一从这里取替身。

`InMemoryQueue` 的实现位于 ``app.pipeline.queue``（brief 的测试代码从该模块
逐字导入，且 app 模块不能反向依赖 tests 包），此处 re-export 聚合，
让测试代码只 import 一个地方。
"""

from app.pipeline.queue import InMemoryQueue, PipeEvent, PipeQueue  # noqa: F401

__all__ = ["InMemoryQueue", "PipeEvent", "PipeQueue"]
