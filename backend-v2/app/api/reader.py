"""引用预览读 API（M6 Task 3）：代码文件窗口 + 文档段落，纯只读。

薄包装，不复刻任何读取逻辑：

- ``/code/read`` 原样透传 :func:`core.reader.read_file`（fs_guard 路径监狱语义与
  500 行窗都在该函数内，越狱由它自己报 ``{"error"}``），API 层只做 error → HTTP 码
  映射：``file not found`` / ``not a file`` 开头 → 404，其余（路径越狱 / 坏行窗）→ 400。
- ``/docs/section`` 用 :func:`doc_search.get_doc_toc` 把 ``(doc_name, anchor)`` 映射到
  ``document_id``，再 :func:`doc_search.read_doc_section` 取段落；映射或段落任一环节
  落空 → 404。

两个下游函数皆同步，经 ``asyncio.to_thread`` 下放线程——**只传位置参数**（to_thread
的 kwargs 走线程包装会被静默吞成 TypeError 的老坑，见 fs_guard/CLAUDE.md）。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.core.doc_search import get_doc_toc, read_doc_section
from app.core.reader import read_file

router = APIRouter(prefix="/v1", tags=["reader"])


@router.get("/code/read")
async def code_read(
    repo: str,
    path: str,
    start_line: int | None = Query(default=None, ge=1),
    end_line: int | None = Query(default=None, ge=1),
) -> dict:
    """读 repo 内文件窗口，返回 read_file 同形 dict（content/total_lines/start_line/end_line/truncated）。"""
    result = await asyncio.to_thread(
        read_file, settings.repos_root, repo, path, start_line, end_line
    )
    if "error" in result:
        msg = str(result["error"])
        code = 404 if msg.startswith(("file not found", "not a file")) else 400
        raise HTTPException(status_code=code, detail=msg)
    return result


@router.get("/docs/section")
async def doc_section(repo: str, doc_name: str, anchor: str) -> dict:
    """按 (doc_name, anchor) 取文档段落正文；TOC 无此映射或段落读不到 → 404。"""
    toc = await asyncio.to_thread(get_doc_toc, repo)
    doc_id = next(
        (
            entry.get("document_id")
            for entry in toc.get("toc", [])
            if entry.get("doc_name") == doc_name and entry.get("anchor") == anchor
        ),
        None,
    )
    if doc_id is None:
        raise HTTPException(status_code=404, detail=f"section 不存在: {doc_name}#{anchor}")
    section = await asyncio.to_thread(read_doc_section, repo, doc_id, anchor)
    if not section or "error" in section or not section.get("content"):
        raise HTTPException(status_code=404, detail=f"section 不存在: {doc_name}#{anchor}")
    return {
        "doc_name": doc_name,
        "anchor": anchor,
        "title": section.get("title", ""),
        "content": section.get("content", ""),
    }
