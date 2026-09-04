"""v2 三档 LLM 客户端：ChatOpenAI 工厂——后续所有 LLM 调用点的唯一构造入口。

移植自旧库 ``app/agent/llm.py::model_for``（M44）+ ``app/clients/model_router.py::endpoint_for``
（v2 无 legacy LLMClient，故 router 层收敛进来；差异：温度按档分、``configured()``
直接走 endpoint_for 而非 legacy 客户端）。三档 = routing（意图分类）/ extraction
（改写·摘要·结构化提取）/ reasoning（推理生成），端点三元组经 ``model_adapter``
字段级回落 ``llm_*``；``MODEL_ROUTES`` 空 = 三档全默认，零行为变更。

一贯契约：未配置（无 key）不报错——调用点以 ``configured()`` 判定后走规则/模板兜底。
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import replace

from langchain_openai import ChatOpenAI

from app.clients.model_adapter import ModelEndpoint, parse_model_routes, resolve_endpoint
from app.core.config import settings

__all__ = ["chat_model_for", "configured", "endpoint_for", "_TIER_MODELS", "_TIER_TEMP"]

#: 按档温度：routing=分类要稳、extraction=提取要贴原文、reasoning=生成可稍放开
_TIER_TEMP = {"routing": 0.0, "extraction": 0.1, "reasoning": 0.3}

#: 实例缓存 key = (task_type, base_url, api_key, model)——同档换端点换实例
_TIER_MODELS: dict[tuple[str, str, str, str], ChatOpenAI] = {}


#: 评测 A/B 请求级模型覆盖（档 → 端点字段子集）。default={}：未 apply 时 .get() 直接空。
#: 值永不原地修改（apply 拷贝入、消费方只读）→ 共享空 dict 默认值安全。
_MODEL_OVERRIDES: ContextVar[dict[str, dict[str, str]]] = ContextVar(
    "coderag_model_overrides", default={})


def apply_model_overrides(overrides: dict[str, dict[str, str]]):
    """按档覆盖端点字段（base_url/api_key/model 子集），返回 reset token（配 reset 用）。

    评测 harness 专用接缝：变体内 set、case 跑完 reset，不污染生产路径
    （缺席 = 空 dict = endpoint_for 原样，零行为变更）。
    """
    return _MODEL_OVERRIDES.set({k: dict(v) for k, v in (overrides or {}).items()})


def reset_model_overrides(token: Token) -> None:
    """恢复 apply 前的覆盖状态（ContextVar token reset）。"""
    _MODEL_OVERRIDES.reset(token)


def endpoint_for(task_type: str) -> ModelEndpoint:
    """解析档位端点（settings.model_routes + 字段级回落 llm_* + 评测 ContextVar 覆盖）。"""
    ep = resolve_endpoint(task_type, parse_model_routes(settings.model_routes))
    override = _MODEL_OVERRIDES.get().get(task_type)
    return replace(ep, **override) if override else ep


def chat_model_for(task_type: str = "reasoning") -> ChatOpenAI:
    """取档位 ChatOpenAI（实例缓存）。

    api_key 合成 ``ep.api_key or "EMPTY"``——哑钥匙防 openai>=1 构造期校验抛（仅当该档
    显式指向端点且全局无 key 时生效；vLLM 接受任意值）。base_url 统一去除尾斜杠
    （resolve_endpoint 已做）。调用点签名与旧库 model_for 完全一致。
    """
    ep = endpoint_for(task_type)
    key = (task_type, ep.base_url, ep.api_key, ep.model)
    if key not in _TIER_MODELS:
        _TIER_MODELS[key] = ChatOpenAI(
            model=ep.model,
            api_key=ep.api_key or "EMPTY",
            base_url=ep.base_url,
            streaming=True,
            temperature=_TIER_TEMP.get(task_type, 0.3),
        )
    return _TIER_MODELS[key]


def configured() -> bool:
    """是否配置了 LLM key（reasoning 档有 key 即视为已配置）。"""
    return bool(endpoint_for("reasoning").api_key)
