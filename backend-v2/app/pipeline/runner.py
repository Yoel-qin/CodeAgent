"""M5 Task 13：runner——从 :class:`~app.pipeline.queue.PipeQueue` 消费事件并分派。

dispatch 表（与 brief 决策一致）：

- ``push`` → :func:`expand_push` → 子事件逐条 enqueue（保序：file 事件按 diff 顺序、
  graph_rebuild 殿后）→ ack 原 push。push 本身不进 event_log——幂等由 file 级账本
  （``uk_pipeline_events_repo_commit_hash_path``）把关，也不计 processed（计 skipped：
  「消费了但不是工作单元」，test_replay 断言 processed==0 依赖此约定）。
- ``file`` → repo/path 越狱校验（非法 skip+ack，Task 12 ⚠️-3——重试不可能成功）→
  ``record_event`` 记账（False=已 DONE 的重复 → skip+ack）→ 扩展名路由：
  ``.java`` → Worker B、文档扩展 → Worker D、其他扩展 skip（记 DONE 闭环账本）→
  ``mark_done`` + commit + ack。
- ``graph_rebuild`` → 同上记账（path 固定 ``"__repo__"``）→ Worker C →
  ``mark_done`` + commit + ack。
- 未知 kind → skip + ack（毒事件不重试不崩）。
- 任何异常 → ``attempts + 1 < settings.pipe_max_attempts`` → 重投（attempts 递增）
  + ack（先重投再 ack：两步间崩溃只会重复投递，不会丢事件）；否则 死信 + 账本
  DEAD + commit + ack。

事务/幂等契约：

- **每事件独立事务**（独立 Session）——一事件失败不连坐；失败路径先回滚本次半途
  写入再记账，重试零副作用。
- **handler 幂等**（Task 11 ⚠️4 语义债的对策）：重投同事件结果一致——B 删旧重建、
  D hash skip、C 全量 replace、账本 uk 去重。
- handler 返回 ``{"skipped": True}``（非 M/A/D status / 非文档扩展）计 skipped，
  其余成功计 processed。
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.fs_guard import PathEscapeError, resolve_repo_path
from app.pipeline.event_log import mark_dead, mark_done, record_event
from app.pipeline.queue import PipeEvent, PipeQueue
from app.pipeline.workers.a_files import expand_push
from app.pipeline.workers.b_entities import process_code_file
from app.pipeline.workers.c_graph import rebuild_graph
from app.pipeline.workers.d_docs import DOC_EXTS, process_doc_file

# graph_rebuild 事件在账本里的固定 path（无单一路径；与 PipelineEvent 模型注释一致）
_REPO_PATH = "__repo__"
_ERROR_MAX = 2000  # last_error 列截断（Text 也别塞无界串）


def run_worker_once(queue: PipeQueue, *, max_events: int = 50) -> dict:
    """消费至多 ``max_events`` 条事件，返回 ``{"processed","skipped","retried","dead"}``。

    非阻塞轮询（``block_ms=0``）：一批空即收——「跑一轮」语义；每事件独立
    Session/事务，本轮内互不连坐。
    """
    stats = {"processed": 0, "skipped": 0, "retried": 0, "dead": 0}
    engine = create_engine(settings.postgres_dsn_sync)
    try:
        seen = 0
        while seen < max_events:
            batch = queue.consume(count=max_events - seen, block_ms=0)
            if not batch:
                break
            for event in batch:
                seen += 1
                _dispatch(queue, engine, event, stats)
    finally:
        engine.dispose()
    return stats


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def _dispatch(queue: PipeQueue, engine, event: PipeEvent, stats: dict) -> None:
    try:
        if event.kind == "push":
            _handle_push(queue, event, stats)
        elif event.kind == "file":
            _handle_file(queue, engine, event, stats)
        elif event.kind == "graph_rebuild":
            _handle_graph(queue, engine, event, stats)
        else:
            logger.warning("runner: 未知事件类型，跳过: kind={!r} id={}",
                           event.kind, event.event_id)
            stats["skipped"] += 1
            queue.ack(event)
    except Exception as exc:  # noqa: BLE001 —— worker 失败绝不向上崩：重试/死信
        _fail(queue, engine, event, exc, stats)


def _handle_push(queue: PipeQueue, event: PipeEvent, stats: dict) -> None:
    """push → 展开 → 逐条 enqueue（保序）→ ack 原 push（见模块 docstring）。"""
    for kind, payload in expand_push(event.payload):
        queue.enqueue(kind, payload, attempts=0)
    queue.ack(event)
    stats["skipped"] += 1


def _handle_file(queue: PipeQueue, engine, event: PipeEvent, stats: dict) -> None:
    key = _ledger_key(event)
    if not _path_ok(key["repo"], key["path"]):
        stats["skipped"] += 1
        queue.ack(event)
        return

    suffix = Path(key["path"]).suffix.lower()
    if suffix == ".java":
        handler = process_code_file
    elif suffix in DOC_EXTS:
        handler = process_doc_file
    else:
        handler = None

    with Session(engine) as session:
        if not record_event(session, event_kind="file", **key):
            session.rollback()  # 丢弃无效 INSERT；已 DONE = 重复消费
            stats["skipped"] += 1
            queue.ack(event)
            return
        if handler is None:
            # 无工作可做的扩展名也闭环账本（不留悬 PENDING）
            mark_done(session, **key)
            session.commit()
            stats["skipped"] += 1
            queue.ack(event)
            return
        result = handler(session, repo=key["repo"], path=key["path"],
                         status=str(event.payload.get("status") or ""))
        mark_done(session, **key)
        session.commit()
    stats["skipped" if result.get("skipped") else "processed"] += 1
    queue.ack(event)


def _handle_graph(queue: PipeQueue, engine, event: PipeEvent, stats: dict) -> None:
    key = _ledger_key(event)
    if not _path_ok(key["repo"], ""):
        stats["skipped"] += 1
        queue.ack(event)
        return

    with Session(engine) as session:
        if not record_event(session, event_kind="graph_rebuild", **key):
            session.rollback()
            stats["skipped"] += 1
            queue.ack(event)
            return
        rebuild_graph(session, repo=key["repo"])
        mark_done(session, **key)
        session.commit()
    stats["processed"] += 1
    queue.ack(event)


# ---------------------------------------------------------------------------
# 失败路径（重试 / 死信）
# ---------------------------------------------------------------------------


def _fail(queue: PipeQueue, engine, event: PipeEvent, exc: Exception, stats: dict) -> None:
    logger.warning("runner: 事件处理失败（attempts={}）: {} | payload={}",
                   event.attempts, exc, event.payload)
    if event.attempts + 1 < settings.pipe_max_attempts:
        # 先重投再 ack——顺序换来「至多重复、不会丢失」
        queue.enqueue(event.kind, event.payload, attempts=event.attempts + 1)
        queue.ack(event)
        stats["retried"] += 1
        return

    # 超限：回滚本次半途写入 → 账本记 DEAD（record_event 幂等，PENDING 行可能已在）
    if event.kind in ("file", "graph_rebuild"):
        with Session(engine) as session:
            if record_event(session, event_kind=event.kind, **_ledger_key(event)):
                mark_dead(session, error=str(exc)[:_ERROR_MAX], **_ledger_key(event))
            session.commit()
    queue.dead_letter(event, str(exc))
    queue.ack(event)
    stats["dead"] += 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ledger_key(event: PipeEvent) -> dict:
    """事件 → 账本三元组 (repo, commit_hash, path)；graph_rebuild 的 path 固定。"""
    payload = event.payload
    return {
        "repo": str(payload.get("repo") or ""),
        "commit_hash": str(payload.get("commit_hash") or ""),
        "path": _REPO_PATH if event.kind == "graph_rebuild"
        else str(payload.get("path") or ""),
    }


def _path_ok(repo: str, path: str) -> bool:
    """repo 单段 + path 不越狱（Task 12 ⚠️-3）；非法 skip+ack（重试不可能成功）。"""
    try:
        resolve_repo_path(settings.repos_root, repo, path)
        return True
    except PathEscapeError as exc:
        logger.warning("runner: 非法 repo/path，跳过: {}", exc)
        return False
