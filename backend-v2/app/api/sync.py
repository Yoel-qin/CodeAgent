"""POST /v1/sync/webhook（Task 12）——离线管道入口：push 事件入 Redis Stream。

校验顺序（brief 决策，先 400 后 422）：

1. ``repo`` 必须是 ``REPOS_ROOT`` 下一级**已存在目录**（防打错仓库名，也防
   ``../`` 越出 repos_root）→ 否则 **400**；
2. ``(before+after)`` 与 ``(commit_hash+files)`` 必须二选一 → 否则 **422**；
3. 惰性构造 :class:`RedisStreamQueue`（流名/组名每次从 settings 读——测试只需
   monkeypatch settings 单例的面）并 ``enqueue("push", payload)``；
   Redis 不可用 → **503** ``{"detail": "queue unavailable"}``。

本端点只入队、不记账（pipeline_events 的记账发生在 worker 侧
``event_log.record_event``），成功返回 ``{"enqueued": True, "event_id": <redis id>}``。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.core.config import settings
from app.pipeline.queue import RedisStreamQueue

router = APIRouter(prefix="/v1/sync", tags=["sync"])


class FileSync(BaseModel):
    path: str
    status: str  # M|A|D|R...


class PushWebhook(BaseModel):
    repo: str
    before: str | None = None
    after: str | None = None
    commit_hash: str | None = None
    files: list[FileSync] | None = None


@router.post("/webhook")
def push_webhook(body: PushWebhook) -> dict:
    _require_known_repo(body.repo)
    _require_one_of_two_shapes(body)

    payload = body.model_dump()
    try:
        q = RedisStreamQueue(
            stream=settings.pipe_stream,
            dead=settings.pipe_dead_stream,
            group=settings.pipe_group,
        )
        event_id = q.enqueue("push", payload)
    except Exception as e:  # noqa: BLE001 —— Redis 挂只降级本端点，不 500 裸栈
        logger.warning("sync webhook: 入队失败（Redis 不可用?）: {}", e)
        raise HTTPException(status_code=503, detail="queue unavailable") from e
    return {"enqueued": True, "event_id": str(event_id)}


def _require_known_repo(repo: str) -> None:
    """repo 必须是 REPOS_ROOT 下一级已存在目录（resolve 后父目录必须就是 repos_root，
    ``../`` / 嵌套多级 / 不存在都算 unknown repo → 400）。"""
    root = Path(settings.repos_root).resolve()
    repo_dir = (root / repo).resolve()
    if repo_dir.parent != root or not repo_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"unknown repo: {repo}")


def _require_one_of_two_shapes(body: PushWebhook) -> None:
    """(before+after) 或 (commit_hash+files) 二选一，缺一半/两套全给都算 422。"""
    git_pair = bool(body.before) and bool(body.after)
    files_pair = bool(body.commit_hash) and bool(body.files)
    if git_pair == files_pair:  # 同真 = 两套全给；同假 = 哪套都不全
        raise HTTPException(
            status_code=422,
            detail="body 需 (before+after) 或 (commit_hash+files) 二选一",
        )
