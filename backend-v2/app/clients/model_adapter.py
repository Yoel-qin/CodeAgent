"""v2 Plan 3 自旧库 M44 原样移植（唯一改动：本行 docstring 注明移植来源）。

M44 ModelAdapter：三档端点三元组（base_url/api_key/model）解析层。

「Adapter」= OpenAI 兼容端点的数据形状 + MODEL_ROUTES 配置解析/字段回落——chat 与
embed 的共性就是端点三元组（两者客户端都已 OpenAI 兼容，无需再包一层客户端抽象）。
纯函数、零 I/O；坏输入软失败（log warning + 降级 {}，与 MCP_SERVERS 同契约），
绝不崩 startup。依赖方向最底层：只 import config，不得 import 任何 client。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import settings

logger = logging.getLogger(__name__)

#: 档位白名单（routing=意图分类 / extraction=改写·摘要·结构化提取 / reasoning=推理生成）
TIERS = ("routing", "extraction", "reasoning")

_FIELDS = ("base_url", "api_key", "model")


@dataclass(frozen=True)
class ModelEndpoint:
    """一个档位的完整服务端点（OpenAI 兼容）。"""

    base_url: str
    api_key: str
    model: str


@lru_cache(maxsize=8)
def parse_model_routes(raw: str) -> dict[str, dict]:
    """解析 MODEL_ROUTES JSON（``{"tier": {"base_url"?, "api_key"?, "model"?}}``）。

    坏 JSON / 顶层非对象 / 值非对象 / 未知档位 → log warning + 忽略对应部分，全坏返回 {}。
    lru_cache 按 raw 字符串缓存：配置 import 期定型（同一值命中缓存），测试 monkeypatch
    换串自动换缓存项（无需手工清缓存）。
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("MODEL_ROUTES 非法 JSON，忽略全部档位覆盖：%s", e)
        return {}
    if not isinstance(obj, dict):
        logger.warning("MODEL_ROUTES 顶层必须是对象 {tier: {...}}，已忽略。")
        return {}
    out: dict[str, dict] = {}
    for tier, entry in obj.items():
        if tier not in TIERS:
            logger.warning("MODEL_ROUTES 未知档位 %r（白名单 %s），已忽略该档。",
                           tier, TIERS)
            continue
        if not isinstance(entry, dict):
            logger.warning("MODEL_ROUTES 档位 %r 的值必须是对象，已忽略该档。", tier)
            continue
        clean = {k: str(entry[k]) for k in _FIELDS if entry.get(k) is not None}
        out[tier] = clean
    return out


def resolve_endpoint(task_type: str,
                     routes: dict[str, dict] | None = None) -> ModelEndpoint:
    """字段级子集合并出档位端点：entry 字段覆盖默认，缺省回落 ``llm_*``。

    回落链：base_url → ``llm_base_url``；api_key → ``llm_api_key``；
    model → ``llm_model_{task_type}`` → ``llm_model``（保留 M42 名字回落链）。
    ``routes=None`` 时自读 ``settings.model_routes``（调用方便利；测试传显式 dict）。
    未知 task_type → ValueError（开发期错配早暴露；运行期调用点只传白名单值）。
    """
    if task_type not in TIERS:
        raise ValueError(f"未知模型档位 {task_type!r}（白名单 {TIERS}）")
    entry = (routes if routes is not None
             else parse_model_routes(settings.model_routes)).get(task_type, {})
    return ModelEndpoint(
        base_url=str(entry.get("base_url") or settings.llm_base_url).rstrip("/"),
        api_key=str(entry.get("api_key") or settings.llm_api_key),
        model=str(entry.get("model")
                 or getattr(settings, f"llm_model_{task_type}", "")
                 or settings.llm_model),
    )
