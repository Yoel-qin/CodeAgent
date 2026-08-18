"""M44 ModelRouter：task_type → ModelEndpoint 路由（薄组合层，无状态模块函数）。

无单例无自带缓存——JSON 解析有 lru_cache（model_adapter.parse_model_routes 按 raw
字符串缓存）、ChatOpenAI 实例缓存在 agent/llm._TIER_MODELS（按 endpoint 三元组）、
LLMClient 构造零开销；无状态即无需单例（区别于 AgentRegistry 等有状态注册表）。
依赖方向：model_router → llm_client → model_adapter（无环；llm_client 的默认端点
解析走 model_adapter 纯函数而非本模块，避免循环导入）。
"""
from __future__ import annotations

from app.clients.llm_client import LLMClient
from app.clients.model_adapter import ModelEndpoint, parse_model_routes, resolve_endpoint
from app.core.config import settings


def endpoint_for(task_type: str) -> ModelEndpoint:
    """解析档位端点（settings.model_routes + 字段级回落）。"""
    return resolve_endpoint(task_type, parse_model_routes(settings.model_routes))


def legacy_client_for(task_type: str) -> LLMClient:
    """legacy httpx 客户端按档位构造。

    api_key 原样透传（空即空）——httpx 路径空 key = configured=False 优雅降级照旧，
    **不套** langgraph 侧 ChatOpenAI 的 "EMPTY" 哑钥匙（那是防 openai>=1 构造期校验）。
    """
    ep = endpoint_for(task_type)
    return LLMClient(base_url=ep.base_url, api_key=ep.api_key, model=ep.model)
