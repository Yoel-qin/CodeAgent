"""资源访问路由（Phase 1.5d/e §资源访问）：
- ``GET /resources/{chunk_id}/table-data``：结构化表格 JSON+HTML+描述。
- ``GET /resources/{chunk_id}/image`` / ``/thumbnail``：图片字节流（查 doc_resources → MinIO），
  供前端 CitationCard 渲染图片缩略图/原图（Phase 1.5e）。
"""
from __future__ import annotations

import asyncio
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas.document import TableDataResponse
from app.services import document_service

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("/{chunk_id}/table-data", response_model=TableDataResponse)
async def table_data(chunk_id: str):
    """返回某 table/table_fragment chunk 的结构化表格（JSON + HTML + 描述）。"""
    item = await asyncio.to_thread(document_service.table_data_for_chunk, chunk_id)
    if item is None:
        raise HTTPException(404, "表格 chunk 不存在或非表格类型")
    return item


async def _stream_image(chunk_id: str, thumbnail: bool):
    res = await asyncio.to_thread(document_service.get_image_for_chunk, chunk_id, thumbnail)
    if res is None:
        raise HTTPException(404, "图片不存在")
    data, mime = res
    return StreamingResponse(io.BytesIO(data), media_type=mime)


@router.get("/{chunk_id}/image")
async def get_image(chunk_id: str):
    """原图字节流。"""
    return await _stream_image(chunk_id, False)


@router.get("/{chunk_id}/thumbnail")
async def get_thumbnail(chunk_id: str):
    """缩略图字节流（无缩略图时回退原图）。"""
    return await _stream_image(chunk_id, True)
