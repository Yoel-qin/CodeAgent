"""M5 worker 包（Task 12 起）——「队列事件 → 管道动作」的各个环节。

- ``a_files``：push 事件展开为 per-file 事件（含 .java 变更时追加 graph_rebuild）。
- ``b_entities``：file(.java) 事件 → 实体/边增量入库与删除（Task 13）。
- ``c_graph``：graph_rebuild 事件 → 全量重建实体/边/度量（Task 13）。
- ``d_docs``：file(文档扩展) 事件 → 文档 ingest / 删除（Task 13）。
- :class:`WorkerError`：worker 侧可重试失败的统一异常——runner（Task 13）捕获后
  attempts+1 重投 / 超 ``pipe_max_attempts`` 落死信，进程不崩。
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.fs_guard import PathEscapeError, resolve_repo_path


class WorkerError(RuntimeError):
    """worker 处理失败（可重试）。runner 捕获走重试/死信，绝不向上崩。"""


def repo_dir_of(repo: str) -> Path:
    """``repos_root/<repo>``（经 fs_guard 解析）；路径越狱折成 :class:`WorkerError`。

    runner 侧已对 repo 做过单段校验（非法事件直接 skip+ack），这里是 worker 直调
    时的防御——统一异常契约（WorkerError → 重试/死信，而非裸 ValueError）。
    """
    try:
        return resolve_repo_path(settings.repos_root, repo)
    except PathEscapeError as exc:
        raise WorkerError(str(exc)) from exc
