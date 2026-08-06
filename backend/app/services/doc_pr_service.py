"""真实文档 PR 落地服务（M21）：approve 后真执行 git + 回滚关 PR。

补 M10→M20 旗舰弧线最后一步——``create_doc_pr`` 之前只装配 PR 载荷（分支名/commit message）、
从不执行 git，提案永远停在 ``PENDING_PUSH``。本模块：

  - :func:`fulfill_doc_update`：approve（``set_proposal_status``）写回 KB（M18）+ 重嵌入（M20）**之后**
    post-commit best-effort 调用。读磁盘文档 → ``original_text``→``rewritten_text`` 替换 → 隔离
    ``git worktree`` 建分支+提交 →（``doc_git_push_enabled`` 且可达 remote）推送 → 回填
    ``commit_sha``/``pr_url``、状态翻 ``PUSHED``（已推送）/``COMMITTED``（仅本地提交）；失败→``PUSH_FAILED``
    （KB 已写回，不受影响）。**永不抛、永不染调用方 error**（镜像 M20 eager 重嵌入的致命约束——
    否则 ``/decide`` 误判 400 掩盖已成功的 KB 写回）。
  - :func:`close_open_doc_pr_for`：回滚联动（替换 ``doc_maintenance_stub``）——按 ``source_commit`` 找
    关联的开放 PR 提案（``PUSHED``/``COMMITTED``），删其分支（+ 远程分支 best-effort）、翻
    ``CLOSED_BY_ROLLBACK``。契约 ``Callable[[str], str|None]`` 不变，自开同步 session（仿 scripts）。

经隔离 ``git worktree`` 操作——**不变异主工作区**（可能脏/共享）；推送（outward-facing）由
``doc_git_push_enabled``（默认关）opt-in，无 GitHub PR API（「关 PR」= 删分支+翻状态，语义本地化）。
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.pipeline import sync_git

# approve 后真 git：取提案 + join doc_files 拿仓库相对 file_path（doc_chunks 无 offset，靠 original_text 唯一定位）。
_PROPOSAL_FOR_GIT_SQL = text(
    "SELECT p.proposal_id, p.rewritten_text, p.original_text, p.branch_name, "
    "p.commit_message, f.file_path "
    "FROM doc_update_proposals p JOIN doc_files f ON f.file_id = p.file_id "
    "WHERE p.proposal_id = :pid"
)
# post-commit 回填（主事务已 commit APPROVED，此独立 UPDATE 翻 PUSHED/COMMITTED/PUSH_FAILED + 回填句柄）。
_FULFILL_SQL = text(
    "UPDATE doc_update_proposals SET status = :status, commit_sha = :sha, "
    "pr_url = :url, error_message = :err WHERE proposal_id = :pid"
)
# 回滚 closer：找该 base 提交关联的开放 PR 提案（PENDING/APPROVED/REJECTED 不算「开放 PR」）。
_OPEN_PR_SQL = text(
    "SELECT proposal_id, branch_name, pr_url FROM doc_update_proposals "
    "WHERE source_commit = :sc AND status IN ('PUSHED', 'COMMITTED')"
)
_CLOSE_PR_SQL = text(
    "UPDATE doc_update_proposals SET status = 'CLOSED_BY_ROLLBACK' WHERE proposal_id = :pid"
)


def _splice(original: str | None, rewritten: str | None, file_text: str) -> str | None:
    """把 ``file_text`` 中的 ``original`` 段落替换为 ``rewritten``；**唯一定位**才替换，否则 None（绝不猜）。

    匹配策略：① ``original`` 为 ``file_text`` 的 verbatim 唯一子串；② CRLF/LF 行尾漂移归一化后唯一
    （磁盘 CRLF、``original``（来自 ``doc_chunks.content``）LF 的常见情况）；③ 否则 None（不写，
    避免错位/破坏文件）。``original``==``rewritten`` / 缺任一 → None。

    复杂漂移（空白归一/重复/跨标题）→ None，由调用方记 ``PUSH_FAILED``（KB 已写回；offset 精确
    捕获 / 整文档重写延后）。纯函数、无 IO，单测友好。
    """
    if not original or not rewritten:
        return None
    if file_text.count(original) == 1:
        return file_text.replace(original, rewritten, 1)
    # CRLF/LF 漂移：全转 LF 匹配，按原文件 EOL 风格回写（rewritten 也先转 LF 再统一，避 \r\r\n）。
    lf_text = file_text.replace("\r\n", "\n")
    rew_lf = rewritten.replace("\r\n", "\n")
    if lf_text.count(original) == 1:
        new_lf = lf_text.replace(original, rew_lf, 1)
        return new_lf.replace("\n", "\r\n") if "\r\n" in file_text else new_lf
    return None


def _fulfill_sync(payload: dict) -> dict:
    """同步真 git（隔离 worktree）：建分支→splice 文件→提交→(可选)推送→拆 worktree。**永不抛**
    （任何异常→ ``PUSH_FAILED`` + error）。

    ``payload`` = {repo, remote, file_relpath, original, rewritten, branch, message,
    author_name, author_email, push_enabled}。返回 ``{git_status, commit_sha, pr_url, error}``，
    ``git_status`` ∈ ``PUSHED``（已推送，pr_url 填）/ ``COMMITTED``（仅本地提交，pr_url 空）/
    ``PUSH_FAILED``（splice/分支/提交/读文件失败；commit_sha/pr_url 空）。
    """
    repo = payload["repo"]
    branch = payload["branch"]
    file_relpath = payload["file_relpath"]
    fail = lambda err: {"git_status": "PUSH_FAILED", "commit_sha": None,  # noqa: E731
                        "pr_url": None, "error": err}
    try:
        base = sync_git.git_head(repo)
    except Exception as e:  # noqa: BLE001  非 git 仓库 / git 不可用
        return fail(f"git_head 失败: {type(e).__name__}: {e}")
    parent = tempfile.mkdtemp(prefix="coderag-wt-")
    wt = str(Path(parent) / "wt")
    wt_added = False
    committed = False
    try:
        try:
            sync_git.add_worktree(repo, wt, branch, base)
            wt_added = True
        except Exception as e:  # noqa: BLE001  分支已存在 / base 不可解析
            return fail(f"worktree add 失败（分支 {branch}）: {type(e).__name__}: {e}")
        full = Path(wt) / file_relpath
        try:
            file_text = full.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return fail(f"读磁盘文件失败 {file_relpath}: {type(e).__name__}: {e}")
        new_text = _splice(payload["original"], payload["rewritten"], file_text)
        if new_text is None:
            return fail(f"无法在 {file_relpath} 唯一定位原文段落（offset 捕获/整文档重写延后）")
        full.write_text(new_text, encoding="utf-8")
        try:
            sha = sync_git.commit_worktree(
                wt, file_relpath, payload["message"],
                payload["author_name"], payload["author_email"],
            )
            committed = True
        except Exception as e:  # noqa: BLE001
            return fail(f"commit 失败: {type(e).__name__}: {e}")
        pr_url = None
        if payload["push_enabled"] and payload["remote"]:
            pr_url = sync_git.push_branch(wt, payload["remote"], branch)  # best-effort → None
        return {
            "git_status": "PUSHED" if pr_url else "COMMITTED",
            "commit_sha": sha, "pr_url": pr_url, "error": None,
        }
    finally:
        if wt_added:
            sync_git.remove_worktree(repo, wt)  # best-effort 注销 worktree
            if not committed:
                # 本调用建的空分支（splice/commit 失败）→ 清理；worktree 已拆故分支可删。
                # 注意：add_worktree 失败（分支已存在）时 wt_added=False，不删既有分支。
                sync_git.delete_branch(repo, branch)
        shutil.rmtree(parent, ignore_errors=True)


async def fulfill_doc_update(session: AsyncSession, proposal_id: int) -> dict:
    """approve 后 post-commit 真 git（隔离 worktree 建分支+提交+可选推送）→ 回填 ``commit_sha``/``pr_url``、
    状态翻 ``PUSHED``/``COMMITTED``（失败→``PUSH_FAILED``）。

    **永不抛、永不染调用方 error**（镜像 M20）。闸门：``doc_git_enabled`` 关 / 无 ``rewritten_text`` /
    无 ``file_path`` / 提案不存在 → 返 ``{git_status: None, ...}``（不执行 git，调用方据此跳过）。
    返回 ``{git_status, commit_sha, pr_url, error}``（``git_status`` ∈
    ``PUSHED``/``COMMITTED``/``PUSH_FAILED``/``None``）。
    """
    outcome = {"git_status": None, "commit_sha": None, "pr_url": None, "error": None}
    if not settings.doc_git_enabled:
        return outcome
    try:
        row = (await session.execute(_PROPOSAL_FOR_GIT_SQL, {"pid": proposal_id})).mappings().first()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[doc_pr] fulfill 取提案失败 pid={proposal_id}: {type(e).__name__}: {e}")
        outcome["error"] = "取提案失败"
        return outcome
    if row is None or not row["rewritten_text"] or not row["file_path"] or not row["branch_name"]:
        return outcome  # 提案不存在 / 无重写内容 / 无文件路径 / 无分支名 → 不执行 git
    payload = {
        "repo": settings.repo_path, "remote": settings.doc_git_remote,
        "file_relpath": row["file_path"], "original": row["original_text"],
        "rewritten": row["rewritten_text"], "branch": row["branch_name"],
        "message": row["commit_message"], "author_name": settings.doc_git_author_name,
        "author_email": settings.doc_git_author_email,
        "push_enabled": settings.doc_git_push_enabled,
    }
    # 全位置参（asyncio.to_thread 不能收 kw-only，见 CLAUDE.md）；_fulfill_sync 自吞所有异常。
    res = await asyncio.to_thread(_fulfill_sync, payload)
    # post-commit 回填（独立 UPDATE + commit；主事务已 commit APPROVED）。
    try:
        await session.execute(_FULFILL_SQL, {
            "status": res["git_status"], "sha": res["commit_sha"], "url": res["pr_url"],
            "err": (res["error"] or "")[:512] or None, "pid": proposal_id,
        })
        await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[doc_pr] fulfill 回填失败 pid={proposal_id}: {type(e).__name__}: {e}")
    outcome.update({k: res.get(k) for k in ("git_status", "commit_sha", "pr_url", "error")})
    return outcome


def close_open_doc_pr_for(source_commit: str) -> str | None:
    """回滚联动（替换 ``doc_maintenance_stub.close_open_doc_pr_for``）：按 ``source_commit`` 找关联的
    开放 PR 提案（``PUSHED``/``COMMITTED``），删其分支（+ 远程分支 best-effort）、翻
    ``CLOSED_BY_ROLLBACK``。返回摘要（如「已关闭 2 个文档 PR：coderag/…, coderag/…」）或 None（无匹配）。

    契约 ``Callable[[str], str|None]`` **不变**（``apply_rollback_restore`` 调用点零改动）；
    自开同步 session（仿 ``scripts/resync_embeddings.py``）。**永不抛**（异常仅 log + 返 None，
    不阻断回滚）。``source_commit`` = 提案创建时捕获的 base 提交（``create_doc_pr`` → ``git_head``）。
    """
    if not source_commit:
        return None
    engine = create_engine(settings.database_url_sync)
    try:
        closed: list[str] = []
        with Session(engine) as s:
            rows = s.execute(_OPEN_PR_SQL, {"sc": source_commit}).mappings().all()
            for r in rows:
                branch = r["branch_name"]
                if branch:
                    sync_git.delete_branch(settings.repo_path, branch)  # best-effort，不抛
                    if r["pr_url"]:
                        sync_git.delete_remote_branch(
                            settings.repo_path, settings.doc_git_remote, branch,
                        )
                s.execute(_CLOSE_PR_SQL, {"pid": r["proposal_id"]})
                closed.append(branch or f"#{r['proposal_id']}")
            s.commit()
        if not closed:
            return None
        return f"已关闭 {len(closed)} 个文档 PR：{', '.join(closed)}"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[doc_pr] close_open_doc_pr_for 失败 source={source_commit}: "
                       f"{type(e).__name__}: {e}")
        return None
    finally:
        engine.dispose()
