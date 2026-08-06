"""文档维护写动作服务（M15）：DOC_MAINTAIN 人工审批**通过后**的「重写文档 + 产出 PR 提案」。

区别于 propose 阶段的只读 ReAct 工具（``app/agent/tools/maintain_tools.py``）——本模块是闸门后
（``apply`` 节点）的服务层写动作，由 ``apply_stale`` 直接调用（确定性编排，非 ReAct）：

  - :func:`generate_doc_update`：取过时文档段落 + 对应代码 → LLM 据代码实际行为重写段落 →
    重写工件写回 MinIO（自包含 markdown 对象，**非整文档覆盖**——repo 扫描的文档无整文件副本）。
  - :func:`create_doc_pr`：装配结构化 PR 载荷（分支名 / commit message / diff）+ 落
    ``doc_update_proposals`` 表（status=``PENDING_PUSH``；无 LLM 重写则 ``PENDING_MANUAL``）。
    **仅产出载荷、不执行真实 git**（留作扩展点）。

二者均「永不抛」（catch + 降级返回），保证 ``apply`` 节点不因重写/PR 失败而中断请求。
"""
from __future__ import annotations

import asyncio
import difflib
import time

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import minio_client
from app.clients.llm_client import llm
from app.core.config import settings
from app.db.models.code import CodeChunk
from app.db.models.doc import DocChunk, DocFile
from app.db.models.history import DocUpdateProposal
from app.pipeline.sync_git import git_head

# ---- 纯 helper（无 IO，单测友好）----

_SLUG_MAX = 40


def _slug(text: str) -> str:
    """章节面包屑 → 分支名友好的短 slug（保留字母/数字/中文，去空白与标点）。"""
    keep = "".join(c for c in text if c.isalnum() or "一" <= c <= "鿿")
    return keep[:_SLUG_MAX] or "section"


def _branch_name(file_id: int | None, heading_path: list[str] | None) -> str:
    """PR 分支名：``coderag/doc-update-{file_id}-{slug(章节)}``。"""
    head = " › ".join(heading_path or [])
    return f"coderag/doc-update-{file_id or 'x'}-{_slug(head)}"


def _commit_message(heading_path: list[str] | None, conversation_id: str | None) -> str:
    """commit message（截断到列长 512）。"""
    head = " › ".join(heading_path or []) or "未命名章节"
    suffix = f"（conversation {conversation_id}）" if conversation_id else ""
    msg = f"docs: 同步过时文档段落（{head}）{suffix}\n\n由 CodeRAG DOC_MAINTAIN Agent 生成"
    return msg[:512]


def _unified_diff(original: str | None, rewritten: str | None) -> str:
    """original→rewritten 的 unified diff（无 IO）。任一侧空 → 空串。"""
    if not original or not rewritten:
        return ""
    diff = difflib.unified_diff(
        original.splitlines(keepends=False),
        rewritten.splitlines(keepends=False),
        fromfile="原文档段落",
        tofile="重写段落",
        lineterm="",
    )
    return "\n".join(diff)


def _build_artifact(
    *, file_path: str | None, heading_path: list[str] | None,
    code_label: str | None, rewritten_text: str,
) -> str:
    """组装自包含 markdown 工件（元信息头 + 重写后段落）。"""
    head = " › ".join(heading_path or []) or "未命名章节"
    return (
        "# 文档更新提案\n\n"
        f"> 文件：{file_path or '未知'}\n"
        f"> 章节：{head}\n"
        f"> 关联代码：{code_label or '未知'}\n"
        f"> 生成时间：{int(time.time())}\n\n"
        f"## 重写后段落\n\n{rewritten_text}\n"
    )


# ---- 数据读取（注入 session，便于单测）----


async def _fetch_doc_section(session: AsyncSession, doc_chunk_id: str) -> dict | None:
    """取文档段落 content/heading_path/file_id/file_path（join doc_files）。无则 None。"""
    row = (await session.execute(
        select(DocChunk.content, DocChunk.heading_path, DocChunk.file_id, DocFile.file_path)
        .join(DocFile, DocFile.file_id == DocChunk.file_id)
        .where(DocChunk.chunk_id == doc_chunk_id)
    )).first()
    if row is None:
        return None
    return {"content": row.content, "heading_path": list(row.heading_path or []),
            "file_id": row.file_id, "file_path": row.file_path}


async def _fetch_code_chunk(session: AsyncSession, code_chunk_id: str) -> dict | None:
    """取代码段 content/class_name/method_name。无则 None。"""
    row = (await session.execute(
        select(CodeChunk.content, CodeChunk.class_name, CodeChunk.method_name)
        .where(CodeChunk.chunk_id == code_chunk_id)
    )).first()
    if row is None:
        return None
    return {"content": row.content, "class_name": row.class_name, "method_name": row.method_name}


# ---- 闸门后写动作 ----


async def generate_doc_update(
    session: AsyncSession, *, doc_chunk_id: str, code_chunk_id: str,
) -> dict:
    """据当前代码 LLM 重写过时文档段落，重写工件写回 MinIO。

    返回 ``{rewritten_ok, rewritten_text, original_text, artifact_key, file_id, file_path,
    heading_path, reason}``。未配置 LLM / chunk 缺失 / LLM 或 MinIO 失败 → ``rewritten_ok=False``
    且不抛（``reason`` 区分 no_llm / chunk_not_found / llm_error / llm_empty / ok）。
    """
    doc = await _fetch_doc_section(session, doc_chunk_id)
    code = await _fetch_code_chunk(session, code_chunk_id)
    result: dict = {
        "rewritten_ok": False, "rewritten_text": None,
        "original_text": doc["content"] if doc else None,
        "artifact_key": None,
        "file_id": doc["file_id"] if doc else None,
        "file_path": doc["file_path"] if doc else None,
        "heading_path": doc["heading_path"] if doc else [],
        "reason": "ok",
    }
    if doc is None or code is None:
        result["reason"] = "chunk_not_found"
        return result
    if not llm.configured:
        result["reason"] = "no_llm"
        return result

    code_label = f"{code['class_name'] or '?'}.{code['method_name'] or '?'}"
    messages = [
        {"role": "system", "content": (
            "你是 CodeRAG 技术文档同步专家。根据【当前代码】的实际行为重写【过时文档段落】，"
            "使其准确反映代码。保持原文的语言（中文）、Markdown 格式与标题层级与口吻；"
            "只输出重写后的段落正文，不要解释、不要加多余的顶层标题。")},
        {"role": "user", "content": (
            f"【过时文档段落】（出处 {doc['file_path']} › {' › '.join(doc['heading_path'])}）：\n{doc['content']}\n\n"
            f"【当前代码】（{code_label}）：\n{code['content']}")},
    ]
    try:
        rewritten = (await llm.chat(messages, temperature=0.2, max_tokens=800)).strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[doc_maintenance] LLM 重写失败: {type(e).__name__}: {e}")
        result["reason"] = "llm_error"
        return result
    if not rewritten:
        result["reason"] = "llm_empty"
        return result

    artifact = _build_artifact(
        file_path=doc["file_path"], heading_path=doc["heading_path"],
        code_label=code_label, rewritten_text=rewritten,
    )
    key = f"{settings.doc_update_artifact_prefix}/{doc['file_id']}/{int(time.time())}.md"
    try:
        minio_client.put_bytes(key, artifact.encode("utf-8"), content_type="text/markdown")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[doc_maintenance] MinIO 写工件失败: {type(e).__name__}: {e}")
        key = None

    result.update({"rewritten_ok": True, "rewritten_text": rewritten, "artifact_key": key})
    return result


async def create_doc_pr(
    session: AsyncSession, *, conversation_id: str | None, file_id: int | None,
    doc_chunk_id: str | None, heading_path: list[str] | None, relation_ids: list[int],
    original_text: str | None, rewritten_text: str | None, artifact_key: str | None,
) -> dict:
    """装配结构化 PR 载荷并落 ``doc_update_proposals`` 行（**仅载荷、不执行 git**）。

    ``rewritten_text`` 为 None（无 LLM）→ status=``PENDING_MANUAL``，否则 ``PENDING_PUSH``。
    永不抛：落库失败 → rollback + 返回 status=``FAILED``。
    """
    branch = _branch_name(file_id, heading_path)
    commit_msg = _commit_message(heading_path, conversation_id)
    status = "PENDING_PUSH" if rewritten_text else "PENDING_MANUAL"
    # M21：捕获提案所据 base 提交（仓库当前 HEAD）→ source_commit，供回滚 closer 匹配关 PR。
    # best-effort（非 git 仓库 / git 不可用 → None），永不阻断提案落库。
    try:
        source_commit = await asyncio.to_thread(git_head, settings.repo_path)
    except Exception:  # noqa: BLE001
        source_commit = None
    try:
        proposal = DocUpdateProposal(
            conversation_id=conversation_id, file_id=file_id, doc_chunk_id=doc_chunk_id,
            heading_path=list(heading_path or []), relation_ids=list(relation_ids or []),
            original_text=original_text, rewritten_text=rewritten_text, artifact_key=artifact_key,
            branch_name=branch, commit_message=commit_msg, status=status, source_commit=source_commit,
        )
        session.add(proposal)
        await session.commit()
        await session.refresh(proposal)
        return {"proposal_id": proposal.proposal_id, "branch_name": branch,
                "commit_message": commit_msg, "status": status, "artifact_key": artifact_key,
                "rewritten_ok": bool(rewritten_text)}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[doc_maintenance] 落 PR 提案失败: {type(e).__name__}: {e}")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"proposal_id": None, "branch_name": branch, "commit_message": commit_msg,
                "status": "FAILED", "artifact_key": artifact_key,
                "rewritten_ok": bool(rewritten_text), "error": str(e)[:200]}
