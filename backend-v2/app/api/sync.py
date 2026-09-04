"""POST /v1/sync/webhook（Task 12）——离线管道入口：push 事件入 Redis Stream。

另有 ``GET /v1/sync/events``（M6 Task 3）：pipeline_events 账本分页只读。

校验顺序（brief 决策，先 400 后 422）：

1. ``repo`` 必须是 ``REPOS_ROOT`` 下一级**已存在目录**（防打错仓库名，也防
   ``../`` 越出 repos_root）→ 否则 **400**；
2. ``(before+after)`` 与 ``(commit_hash+files)`` 必须二选一 → 否则 **422**；
3. 惰性构造 :class:`RedisStreamQueue`（流名/组名每次从 settings 读——测试只需
   monkeypatch settings 单例的面）并 ``enqueue("push", payload)``；
   Redis 不可用 → **503** ``{"detail": "queue unavailable"}``。

本端点只入队、不记账（pipeline_events 的记账发生在 worker 侧
``event_log.record_event``），成功返回 ``{"enqueued": True, "event_id": <redis id>}``。

M9：RBAC on 时 webhook 加 repo 门（不可见 repo → 403，router 级 sync 类门之外再补）；
``/events`` 指定 repo 同门、未指定时按可见集过滤（同 documents 模式）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import ensure_repo_allowed, get_current_user, require_class
from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models.pipeline import PipelineEvent
from app.pipeline.queue import RedisStreamQueue

router = APIRouter(prefix="/v1/sync", tags=["sync"], dependencies=[Depends(require_class("sync"))])


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
def push_webhook(body: PushWebhook, user: dict = Depends(get_current_user)) -> dict:
    ensure_repo_allowed(user, body.repo)
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


@router.get("/events")
async def list_events(
    repo: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(PENDING|DONE|DEAD)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
) -> dict:
    """管道账本分页只读（id 倒序）；repo/status 可选过滤，越界参数 → 422。

    total 与页数据分开取（同 document_service：count(*) 才是过滤后的全量行数）。
    M9：RBAC on 时指定 repo 不在可见集 → 403，未指定 → 只出可见集。
    """
    repos_filter: list[str] | None = None
    if settings.rbac_enabled:
        allowed = (user.get("allowed_scopes") or {}).get("repos") or []
        if "*" not in allowed:
            if repo is not None:
                ensure_repo_allowed(user, repo)
            else:
                repos_filter = sorted(allowed)
    count_stmt = select(func.count()).select_from(PipelineEvent)
    stmt = select(PipelineEvent).order_by(PipelineEvent.id.desc())
    if repo:
        count_stmt = count_stmt.where(PipelineEvent.repo == repo)
        stmt = stmt.where(PipelineEvent.repo == repo)
    if repos_filter is not None:
        count_stmt = count_stmt.where(PipelineEvent.repo.in_(repos_filter))
        stmt = stmt.where(PipelineEvent.repo.in_(repos_filter))
    if status:
        count_stmt = count_stmt.where(PipelineEvent.status == status)
        stmt = stmt.where(PipelineEvent.status == status)

    async with SessionLocal() as session:
        total = (await session.execute(count_stmt)).scalar_one()
        rows = (
            (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
        )
    return {
        "total": total,
        "items": [
            {
                "id": e.id,
                "repo": e.repo,
                "commit_hash": e.commit_hash,
                "path": e.path,
                "event_kind": e.event_kind,
                "status": e.status,
                "attempts": e.attempts,
                "last_error": e.last_error,
                "created_at": e.created_at,
                "updated_at": e.updated_at,
            }
            for e in rows
        ],
    }
