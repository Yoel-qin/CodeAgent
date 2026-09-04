"""Router：``query_analysis`` 节点 + ``decide_route`` 纯函数（Plan 3 Task 6）——图的第一站。

把用户 query 归到四类意图之一并决定路由目标，写回
``intent / confidence / simple_fact / reason / route`` 五个状态键——Task 9 的
conditional edge 只读 ``state["route"]``：

- ``codenav`` → 代码导航 Agent（M6）；``docqa`` → 文档问答 Agent（M5）
- ``clarify`` → 追问兜底（Task 7；模型不确定 ``confidence < 0.7``）
- ``retrieve`` → 纯检索兜底（Task 7；``None`` / 简单事实 / web——web Agent 是
  V2-M9，当前一律 retrieve 兜底）

双路分类，规则路兜底、**永不抛**：

1. **LLM 路**（``configured()`` 真）：routing 档 ``with_structured_output(RouteDecision)``
   包 ``asyncio.wait_for(…, 3.0)``——超时 / 结构化解析失败 / 任何异常一律吞掉转规则
   （沿 Plan 1「未配置不报错」契约的失败侧延伸）。
2. **规则路**（``rule_classify``）：关键词规则，无 key 或 LLM 失败时的唯一路径；
   ``simple_fact`` 恒 False（简化——该标志只由 LLM 路产出）。

brief 适配（有据偏差，须带入评审）：plan 文字给规则命中 conf **0.6**，但其自带
逐字测试 ``test_node_no_key_uses_rules`` 断言规则分类的 code 查询
``route == "codenav"``，而同 plan 的 ``decide_route`` 真值表规定
``confidence < 0.7 → "clarify"``——0.6 必然 clarify，两条要求自相矛盾。取逐字
测试（验收门）为准：规则命中 code/doc 置 conf **0.8**（> 0.7 路由门、< 0.9
simple_fact 门），无 key 降级才能真正路由而非全部追问；关键词清单与正则
``([A-Z][a-z0-9]+){2,}`` 仍逐字照 brief。
"""
# 注意：本模块**不**加 ``from __future__ import annotations``——langgraph 按运行时注解
# 对象识别节点可注入的 ``config`` 形参，字符串化的 ``"RunnableConfig | None"`` 不在其
# 白名单 → config 被静默丢弃（configurable 里的 session/cost/top_k 全落空）+ UserWarning；
# 真注解对象 ``RunnableConfig | None == Optional[RunnableConfig]`` 才匹配（Task 9 实测）。
import asyncio
import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, Field

from app.agent.callbacks import CostCallbackHandler
from app.agent.state import AgentState
from app.clients.llm import chat_model_for, configured

__all__ = ["AgentState", "RouteDecision", "decide_route", "query_analysis_node", "rule_classify"]

# ── 常量 ──────────────────────────────────────────────────────────────────

#: LLM 分类超时（秒）——routing 档只做一次小分类，3s 不回即转规则
_LLM_TIMEOUT_S = 3.0

#: rule_classify 置信度：关键词命中（见模块 docstring 的 brief 适配说明）与未命中
_RULE_HIT_CONFIDENCE = 0.8
_RULE_MISS_CONFIDENCE = 0.5

#: 代码信号：PascalCase 类名（至少两个驼峰段）或扩展名/中文关键词
_PASCAL_CASE_RE = re.compile(r"([A-Z][a-z0-9]+){2,}")
_CODE_KEYWORDS = (".java", "源码", "方法", "调用链", "实现")
_DOC_KEYWORDS = ("文档", "手册", "教程", "配置说明", "怎么使用")

# ── 结构化分类结果 ────────────────────────────────────────────────────────


class RouteDecision(BaseModel):
    """一次意图分类的结论（LLM 结构化输出 schema，也是 rule_classify 的返回形状）。"""

    intent: Literal["code", "doc", "web", "other"]
    confidence: float = Field(ge=0.0, le=1.0, description="0..1 分类把握")
    simple_fact: bool = False
    reason: str = ""


# ── 纯函数 ────────────────────────────────────────────────────────────────


def decide_route(d: RouteDecision | None) -> str:
    """真值表：decision → 路由目标（纯函数，Task 9 conditional edge 的唯一依据）。

    ``None → retrieve``；简单事实（高把握）→ ``retrieve``；低把握（< 0.7）→
    ``clarify``；再按 intent 分派 code/doc；web/other 高把握也落 ``retrieve``
    （web Agent 是 V2-M9，当前一律 retrieve 兜底）。
    """
    if d is None:
        return "retrieve"
    if d.simple_fact and d.confidence >= 0.9:
        return "retrieve"
    if d.confidence < 0.7:
        return "clarify"
    if d.intent == "code":
        return "codenav"
    if d.intent == "doc":
        return "docqa"
    return "retrieve"


def rule_classify(query: str) -> RouteDecision:
    """关键词规则分类（无 key / LLM 失败时的兜底；``simple_fact`` 恒 False）。

    code 优先于 doc（「CommitLog 的文档在哪」这类混合问法倾向代码定位）。
    """
    q = query or ""
    if _PASCAL_CASE_RE.search(q) or any(k in q for k in _CODE_KEYWORDS):
        return RouteDecision(intent="code", confidence=_RULE_HIT_CONFIDENCE,
                             reason="规则命中：代码标识符/关键词")
    if any(k in q for k in _DOC_KEYWORDS):
        return RouteDecision(intent="doc", confidence=_RULE_HIT_CONFIDENCE,
                             reason="规则命中：文档关键词")
    return RouteDecision(intent="other", confidence=_RULE_MISS_CONFIDENCE,
                         reason="规则未命中，默认其他")


# ── LLM 路 ────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是代码知识库（CodeRAG）的意图分类器。根据用户问题只输出 json 对象，字段：
- intent："code"（问代码位置/实现/调用链）| "doc"（问文档/手册/教程/配置说明）| "web"（需联网的时效信息）| "other"（闲聊等）
- confidence：0 到 1 之间的把握
- simple_fact：是否为与知识库无关、无需检索即可回答的简单事实
- reason：一句话中文理由

示例：
Q: DefaultMQProducer 的 send 方法在哪个类里实现
A: {"intent": "code", "confidence": 0.92, "simple_fact": false, "reason": "问类方法的实现位置"}

Q: 刷盘机制在文档里是怎么写的
A: {"intent": "doc", "confidence": 0.88, "simple_fact": false, "reason": "问文档章节内容"}

Q: 今天天气怎么样
A: {"intent": "other", "confidence": 0.95, "simple_fact": true, "reason": "与知识库无关的日常问题"}"""


def _messages(query: str) -> list:
    return [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=query)]


def _cost_callbacks(config: RunnableConfig | None) -> dict:
    """LLM 调用挂账：``configurable["cost"]`` 有 → 回调 dict；缺席 → ``{}``（零行为变）。

    与 :func:`app.agent.nodes._cost_callbacks` 同构（Task 10 评审遗留的同名 helper）；
    Router 分类此前漏挂——图里唯一没进预算账本的 LLM 调用就是它（终审 I-1）。
    """
    cost = (config or {}).get("configurable", {}).get("cost")
    return {"callbacks": [CostCallbackHandler(cost)]} if cost is not None else {}


async def _llm_classify(query: str, config: RunnableConfig | None = None) -> RouteDecision | None:
    """routing 档结构化分类；超时/异常返回 ``None``（调用点转规则），永不抛。

    ``method="json_mode"``（M4 验收实测修正）：DeepSeek 对 ``json_schema`` 一律
    ``400 This response_format type is unavailable now``，thinking 档（v4-flash）还禁
    ``tool_choice``（function_calling 亦 400）——唯一双模型可用的是 ``json_mode``，
    且要求提示词含小写 ``json``（大小写敏感，系统提示词已改写满足）。
    """
    try:
        model = chat_model_for("routing").with_structured_output(RouteDecision, method="json_mode")
        return await asyncio.wait_for(
            model.ainvoke(_messages(query), config=_cost_callbacks(config)), _LLM_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 —— 分类失败转规则兜底，请求不破
        logger.warning("query_analysis: routing 档分类失败，转规则兜底: {}", e)
        return None


# ── 图节点 ────────────────────────────────────────────────────────────────


async def query_analysis_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """图入口节点：意图分类 + 路由决策，返回部分更新写回 AgentState。

    ``configured()`` 真 → 先试 LLM 路（失败/超时转规则），假 → 直接规则；
    返回 ``{**decision 字段, "route": decide_route(decision)}``。LLM 分类经
    ``_cost_callbacks`` 挂进 ``configurable["cost"]`` 预算账本（终审 I-1）。

    M7 起可选 trace（``configurable["trace"]``）：分类调用外包一个 ``route`` span，
    attrs 携 intent/confidence/route/reason（LLM 路与规则路统一口径）；缺席零行为变更。

    M9 RBAC 门 1：``configurable["scopes"]`` 非 None 且 intent 的读域
    （code/doc）不在 ``scopes["kinds"]`` 时，无权 intent 不进场景 Agent——route
    改判 ``retrieve``（纯检索兜底仍受门 2 按域跳路约束）；``decision.intent``
    保持原值（诚实记录被门控的分类结论，span attrs 补 ``gated``）。scopes
    缺席（RBAC off）零行为变更。
    """
    query = state.get("query", "") or ""
    trace = (config or {}).get("configurable", {}).get("trace")
    sid = trace.start("route", "query_analysis") if trace is not None else None
    decision: RouteDecision | None = None
    if configured():
        decision = await _llm_classify(query, config)
    if decision is None:
        decision = rule_classify(query)
    route = decide_route(decision)
    # M9 RBAC 门 1：无读域权限的 intent 不进场景 Agent（v1 模式——reroute retrieve）；
    # decision.intent 保持原值（诚实记录被门控的分类结论）
    scopes = (config or {}).get("configurable", {}).get("scopes")
    gated = False
    if scopes is not None:
        kinds = scopes.get("kinds") or set()
        if (decision.intent == "code" and "code" not in kinds) or (
                decision.intent == "doc" and "doc" not in kinds):
            route = "retrieve"
            gated = True
    logger.debug("query_analysis: intent={} conf={} route={} reason={}",
                 decision.intent, decision.confidence, route, decision.reason)
    if trace is not None:
        trace.end(sid, attrs={"intent": decision.intent, "confidence": decision.confidence,
                              "route": route, "reason": decision.reason, "gated": gated or None})
    return {**decision.model_dump(), "route": route}
