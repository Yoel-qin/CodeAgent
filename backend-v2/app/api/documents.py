"""文档读 API（M6 Task 1）：列表 + 详情节列表，纯只读。

session 用法沿 chat.py（``async with SessionLocal()``）；列表项带 section_count
（前端文档页无需再按篇请求节列表）。参数边界在 Query 上声明：limit 1..200 /
offset ≥0，越界 → 422（防负 offset / 巨 limit 打到 PG 报 500）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.db.base import SessionLocal
from app.services import document_service

router = APIRouter(prefix="/v1/documents", tags=["documents"])


@router.get("")
async def list_documents(
    repo: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """文档列表（id 倒序）；repo 缺省 = 不过滤。"""
    async with SessionLocal() as session:
        total, rows = await document_service.list_documents(
            session, repo=repo, limit=limit, offset=offset
        )
    return {
        "total": total,
        "items": [
            {
                "id": d.id,
                "repo": d.repo,
                "doc_name": d.doc_name,
                "module": d.module,
                "doc_type": d.doc_type,
                "status": d.status,
                "section_count": count,
                "created_at": d.created_at,
            }
            for d, count in rows
        ],
    }


@router.get("/{document_id}/sections")
async def document_sections(document_id: int) -> dict:
    """文档详情 + 节列表（order_index 升序）；不存在 → 404。"""
    async with SessionLocal() as session:
        detail = await document_service.get_document_with_sections(session, document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="document 不存在")
    return detail
