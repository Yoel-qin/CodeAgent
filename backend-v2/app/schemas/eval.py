"""评测请求模型（M8）。variant dict 形状与 harness.EvalVariant 字段一一对应
（service 侧 ``EvalVariant(**v)`` 直构）。"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class EvalVariantRequest(BaseModel):
    name: str = Field(default="baseline", min_length=1, max_length=64)
    rounds_code: int | None = Field(default=None, ge=1, le=30)
    rounds_doc: int | None = Field(default=None, ge=1, le=30)
    code_no_graph: bool = False
    model_reasoning: str | None = Field(default=None, max_length=128)
    top_k: int | None = Field(default=None, ge=1, le=50)


class EvalRunRequest(BaseModel):
    """POST /v1/eval/run 请求体。variants 空 = 单 baseline 跑批。"""

    repo: str | None = Field(default=None, max_length=256)
    variants: list[EvalVariantRequest] = Field(default_factory=list)
    judge: bool = False
    golden_path: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _unique_variant_names(self):
        names = [v.name for v in self.variants]
        if len(names) != len(set(names)):
            raise ValueError("variant name 重复")
        return self
