"""Worker B（Task 13）：file(.java) 事件 → 实体/边增量入库与删除。

幂等语义（重投同事件结果一致）：A/M 一律「先 DELETE 同 (repo, file_path) 旧实体
（级联删边）再 upsert」——行号漂移会让 uk (repo, class_name, method_name,
file_path, start_line) 残留旧行，删旧重建而不是只增；D 删边+删实体，重复执行是
空操作（DELETE 0 行）。

status 非 M/A/D → ``{"skipped": True}``（Task 12 评审 ⚠️-2：rename 行已由 Worker A
拆成 D+A，其余状态没有可执行的 ingest 动作，跳过不 raise）。文件缺失 / 读取失败 /
解析失败 → :class:`WorkerError`（runner 捕获走重试/死信）。
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.db.models.code_graph import CallEdge, CodeEntity
from app.pipeline.ingest_code import (
    _infer_module,
    entities_from_parsed,
    upsert_entities,
)
from app.pipeline.parsing.code_parser import parse_java
from app.pipeline.workers import WorkerError, repo_dir_of

_MUTABLE_STATUS = ("A", "M", "D")


def process_code_file(session: Session, *, repo: str, path: str, status: str) -> dict:
    """单个 .java 变更的增量 ingest（不 commit——runner 控制每事件事务边界）。"""
    if status not in _MUTABLE_STATUS:
        return {"skipped": True, "reason": f"unsupported status: {status!r}"}

    if status == "D":
        dropped = _drop_file_state(session, repo=repo, path=path)
        return {"deleted": True, "entities_dropped": dropped, "skipped": False}

    fp = repo_dir_of(repo) / path
    if not fp.is_file():
        raise WorkerError(f"代码文件不存在: {fp}")
    try:
        src = fp.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkerError(f"代码文件读取失败: {fp}: {exc}") from exc
    try:
        pf = parse_java(src, path)
    except Exception as exc:  # noqa: BLE001 —— 解析器任意异常折成可重试 WorkerError
        raise WorkerError(f"Java 解析失败: {path}: {exc}") from exc

    # 先删旧实体（级联删边/度量）再 upsert：行号漂移防残留（见模块 docstring）
    dropped = _drop_file_state(session, repo=repo, path=path)
    result = upsert_entities(
        session,
        entities_from_parsed(pf, repo=repo, module=_infer_module(path)),
    )
    logger.debug("[b_entities] {}/{}: -{} old, +{}/{} new",
                 repo, path, dropped, result["inserted"], result["updated"])
    return {
        "inserted": result["inserted"],
        "updated": result["updated"],
        "entities_dropped": dropped,
        "skipped": False,
    }


def _drop_file_state(session: Session, *, repo: str, path: str) -> int:
    """删同 (repo, file_path) 旧实体；先删两端边再删实体，返回删除实体数。

    （call_edges/code_metrics 对 code_entities 是 ondelete=CASCADE，显式删边是
    brief 契约 + 让删除量可观测。）
    """
    ids = select(CodeEntity.id).where(CodeEntity.repo == repo, CodeEntity.file_path == path)
    session.execute(delete(CallEdge).where(
        or_(CallEdge.caller_id.in_(ids), CallEdge.callee_id.in_(ids))
    ))
    return session.execute(
        delete(CodeEntity).where(CodeEntity.repo == repo, CodeEntity.file_path == path)
    ).rowcount or 0
