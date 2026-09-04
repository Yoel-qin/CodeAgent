"""评测 API（M8）：POST /run（同步长跑，前端单请求 600s 超时）+ GET 列表/详情。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_class
from app.schemas.eval import EvalRunRequest
from app.services import eval_service

router = APIRouter(prefix="/v1/eval", tags=["eval"], dependencies=[Depends(require_class("eval"))])


@router.post("/run")
async def run(req: EvalRunRequest) -> dict:
    """跑一次评测（variants 空 = 单 baseline；judge=问答 4 维 LLM 评判）。"""
    return await eval_service.run_and_persist(
        repo=req.repo, variants=[v.model_dump() for v in req.variants],
        judge=req.judge, golden_path=req.golden_path, trigger="api")


@router.get("/runs")
async def runs(limit: int = Query(default=50, ge=1, le=200),
               offset: int = Query(default=0, ge=0)) -> dict:
    return await eval_service.list_runs(limit=limit, offset=offset)


@router.get("/runs/{run_id}")
async def run_detail(run_id: int) -> dict:
    detail = await eval_service.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"eval run 不存在: {run_id}")
    return detail
