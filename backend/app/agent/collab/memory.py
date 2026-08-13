"""M35 协作 WorkingMemory 业务辅助 + 结构化提取 schema。

构造器（``make_*``）统一字段名，供三层节点构造 hypotheses/findings/suggestions 条目；
pydantic schema（``*List``）供节点用 ``with_structured_output`` 从 LLM 提取结构化产出。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# ---- 条目构造器（dict，进 AgentState 的 collab_* reducer 字段）----


def make_hypothesis(hypothesis: str, *, confidence: str = "中", rationale: str = "") -> dict:
    return {"hypothesis": hypothesis, "confidence": confidence, "rationale": rationale}


def make_finding(chunk_id: str, finding: str, *, hypothesis_id: int | None = None,
                 verdict: str = "supports") -> dict:
    return {"chunk_id": chunk_id, "finding": finding,
            "hypothesis_id": hypothesis_id, "verdict": verdict}


def make_suggestion(suggestion: str, *, doc_chunk_id: str | None = None,
                    rationale: str = "") -> dict:
    return {"suggestion": suggestion, "doc_chunk_id": doc_chunk_id, "rationale": rationale}


# ---- 结构化提取 schema（with_structured_output 用）----


class HypothesisItem(BaseModel):
    """一条诊断假设。"""
    hypothesis: str = Field(description="诊断假设的一句话陈述")
    confidence: str = Field(default="中", description="置信度：高/中/低")
    rationale: str = Field(default="", description="依据简述")


class HypothesisList(BaseModel):
    """诊断层产出的假设清单（通常 2-4 条）。"""
    hypotheses: list[HypothesisItem]


class FindingItem(BaseModel):
    """一条代码验证结论。"""
    chunk_id: str = Field(description="验证所依据的代码 chunk_id")
    finding: str = Field(description="在代码中发现的事实")
    hypothesis_id: int = Field(default=0, description="支持/反驳的假设序号（0 起）")
    verdict: str = Field(default="supports", description="supports 或 refutes")


class FindingList(BaseModel):
    """验证层产出的结论清单。"""
    findings: list[FindingItem]


class SuggestionItem(BaseModel):
    """一条调优建议。"""
    suggestion: str = Field(description="可操作的调优建议")
    doc_chunk_id: str | None = Field(default=None, description="依据的文档 chunk_id（可空）")
    rationale: str = Field(default="", description="依据简述")


class SuggestionList(BaseModel):
    """调优层产出的建议清单。"""
    suggestions: list[SuggestionItem]
