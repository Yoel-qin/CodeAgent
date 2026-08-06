"""主动腐化巡检模块的响应 schema（M16 报告 / M17 批量重写审批队列）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class StalenessFinding(BaseModel):
    relation_id: int
    relation_type: str | None = None
    anchor_key: str | None = None
    stale_reason: str | None = None
    updated_at: datetime | None = None


class StalenessSourceBreakdown(BaseModel):
    sweep: int = 0      # 巡检自动标记（stale_reason LIKE 'SWEEP:%'）
    deleted: int = 0    # soft_delete 代码删除标记（'DELETED:%'）
    other: int = 0      # HITL 审批 / 其他来源


class StalenessReport(BaseModel):
    total: int
    stale: int
    by_source: StalenessSourceBreakdown = StalenessSourceBreakdown()
    recent: list[StalenessFinding] = []


# ---- M17：SWEEP 批量重写 → 审批队列 ----


class SweepRewriteRequest(BaseModel):
    """``POST /v1/staleness/sweep-rewrite``：为 top-N SWEEP 标记的过时 doc 批量生成重写提案。"""

    top_n: int = Field(10, ge=1, le=50)


class ProposalItem(BaseModel):
    """单条 doc 更新提案（doc_update_proposals 行投影）。"""

    proposal_id: int | None = None
    conversation_id: str | None = None
    file_id: int | None = None
    doc_chunk_id: str | None = None
    heading_path: list[str] = []
    relation_ids: list[int] = []
    status: str | None = None
    rewritten_ok: bool = False
    artifact_key: str | None = None
    branch_name: str | None = None
    commit_sha: str | None = None          # M21 真 git 产出的分支提交（PUSHED/COMMITTED 填）
    pr_url: str | None = None              # M21 推送目标 URL（PUSHED 填）
    # M19 审批 UI 预览用：list 端点经 _proposal_to_dict 填充；sweep-rewrite 项不走该函数→缺省 None。
    rewritten_text: str | None = None
    original_text: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SweepRewriteResponse(BaseModel):
    """批量重写结果：扫描/去重/幂等跳过/各状态计数 + 本轮产出的提案。"""

    scanned: int = 0           # 池中命中的 SWEEP 过时关系数
    slots: int = 0             # 按 doc_chunk_id 去重后的文档段落数
    skipped_existing: int = 0  # 已有 active 提案（幂等守卫）跳过的段落数
    rewritten: int = 0         # LLM 重写成功数
    pending_push: int = 0
    pending_manual: int = 0
    failed: int = 0
    proposals: list[ProposalItem] = []
    error: str | None = None


class ProposalListResponse(BaseModel):
    total: int = 0
    items: list[ProposalItem] = []


class ProposalDecisionRequest(BaseModel):
    """``POST /v1/staleness/proposals/{id}/decide``：人工审批（APPROVED 触发真写回；REJECTED 仅翻转）。"""

    decision: Literal["APPROVED", "REJECTED"]


class ProposalDecisionResponse(BaseModel):
    """``POST /v1/staleness/proposals/{id}/decide`` 结果：approve 时 ``applied=True`` 报告写回情况。"""

    proposal_id: int
    status: str
    applied: bool = False                 # APPROVED 且成功写回 doc_chunks 才为 True
    doc_chunk_id: str | None = None       # 写回的目标 doc chunk（applied=False 时为 None）
    relations_cleared: int = 0            # 清掉过时标记的锚点关系数
    reembed_status: Literal["synced", "failed", "lazy"] | None = None  # M20 eager 重嵌入结果（REJECTED/未apply=None）
    git_status: Literal["PUSHED", "COMMITTED", "PUSH_FAILED"] | None = None  # M21 真 git 结果（REJECTED/未apply/disabled=None）
    commit_sha: str | None = None         # M21 分支提交 sha（PUSHED/COMMITTED 填）
    pr_url: str | None = None             # M21 推送目标 URL（PUSHED 填）
