"""M36 DomainPack pydantic schema（领域知识包数据模型）。

领域字段默认空——骨架包（占位 yaml）与部分包都能加载，未来字段缺省不破坏解析。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Manifest(BaseModel):
    """包元信息。"""
    name: str
    target_repo: str                       # 目标仓库标识，如 apache/rocketmq
    version: str = ""
    active_agents: list[str] = Field(default_factory=list)   # M37 领域 Agent 名
    description: str = ""


class TraceTemplate(BaseModel):
    name: str
    scenario: str = ""
    method_sequence: list[str] = Field(default_factory=list)
    notes: str = ""


class DiagnosisTree(BaseModel):
    name: str
    symptoms: list[str] = Field(default_factory=list)
    hypothesis_checks: list[str] = Field(default_factory=list)
    tuning_hints: list[str] = Field(default_factory=list)


class TuningRule(BaseModel):
    scenario: str
    parameter: str
    suggestion: str
    tradeoff: str = ""
    code_ref: str = ""


class ConfigItem(BaseModel):
    key: str
    description: str = ""
    allowed_values: list[str] = Field(default_factory=list)


class DomainPack(BaseModel):
    manifest: Manifest
    trace_templates: list[TraceTemplate] = Field(default_factory=list)
    diagnosis_trees: list[DiagnosisTree] = Field(default_factory=list)
    tuning_rules: list[TuningRule] = Field(default_factory=list)
    config_registry: list[ConfigItem] = Field(default_factory=list)
    prompts: dict[str, str] = Field(default_factory=dict)   # 文件名→内容（prompts/*.md）
