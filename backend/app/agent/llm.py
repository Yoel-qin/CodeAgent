"""Agent 层 LLM：ChatOpenAI 工厂 + 意图分类 + token→SSE 回调。

复用 settings.llm_*（DeepSeek，OpenAI 兼容，原生 function calling）。CodeRAG 一贯模式：
未配置 / 调用失败 → 规则兜底，绝不抛。
"""
from __future__ import annotations

from typing import Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.clients.llm_client import llm as legacy_llm
from app.core.config import settings

# 意图标签：router 据此条件路由（code → 代码理解；doc → 文档问答；graph → 变更影响；
# bug → 缺陷诊断；review → 代码审查；test → 测试生成；mixed/chitchat → retrieve 兜底）
IntentLabel = Literal["code", "doc", "graph", "bug", "review", "test", "mixed", "chitchat"]

_CHAT_MODEL: ChatOpenAI | None = None


def get_chat_model() -> ChatOpenAI:
    """惰性单例 ChatOpenAI（指向 DeepSeek，streaming 供 token 回调）。"""
    global _CHAT_MODEL
    if _CHAT_MODEL is None:
        _CHAT_MODEL = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            streaming=True,
            temperature=0.3,
        )
    return _CHAT_MODEL


def configured() -> bool:
    """是否配置了 LLM key（复用 legacy 客户端的判定）。"""
    return bool(legacy_llm.configured)


# ---- 意图分类（with_structured_output + 规则兜底）----


class IntentSchema(BaseModel):
    """用户提问意图。code=代码理解/方法逻辑/调用关系；doc=文档/配置/用法；graph=依赖/结构；
    bug=报错/异常/崩溃/为何失败/根因诊断；review=代码审查/质量评估/潜在问题/改进建议/重构；
    test=生成测试/单元测试/JUnit/测试用例；mixed=代码+文档混合；chitchat=闲聊/寒暄。"""

    intent: IntentLabel = Field(description="用户提问的意图类别")


_INTENT_SYS = (
    "你是意图分类器。判断用户在 Java 代码知识库中的提问意图，只输出一个类别：\n"
    "code=理解方法/类/逻辑/调用关系/实现；doc=查文档/配置/用法说明；"
    "graph=模块依赖/调用结构/影响范围；bug=排查报错/异常/崩溃/为何失败/根因诊断；"
    "review=代码审查/质量评估/潜在问题/改进建议/重构；"
    "test=生成测试/单元测试/JUnit/测试用例/测试代码；"
    "mixed=同时涉及代码与文档；chitchat=寒暄/与代码库无关。\n"
    "默认偏向 code（这是一个代码知识库）。"
)

_CODE_HINTS = ("方法", "函数", "类", "接口", "调用", "实现", "逻辑", "这段代码", "源码",
               "method", "function", "class", "调用链", "做了什么", "为什么这么")
_DOC_HINTS = ("文档", "配置", "怎么用", "怎么配置", "使用说明", "参数", "等级", "重试", "手册")
_GRAPH_HINTS = ("依赖", "模块", "结构", "影响", "下游", "上游", "包")
# 缺陷诊断强信号（不含裸「异常」——避免误吞 "事务消息异常处理" 这类 doc 查询）
_BUG_HINTS = ("报错", "崩溃", "空指针", "npe", "nullpointer", "stacktrace", "栈溢出",
              "为什么失败", "为什么不对", "为什么出错", "why fail", "出问题")
# 代码审查强信号（主动评估质量/找问题/改进建议；区别于 bug 的「已报告失败」）
_REVIEW_HINTS = ("审查", "review", "代码质量", "改进建议", "优化建议", "潜在问题", "风险点",
                 "重构", "可读性", "代码规范", "code review", "评审")
# 测试生成强信号（主动生成测试代码）。用中文「测试」(CJK 零英文误匹配) + 英文具体词；
# **不收裸 test/mock**——否则子串匹配会把 "latest changes"(la**test**) 误判为 test。
_TEST_HINTS = ("测试", "junit", "mockito", "unit test", "test case", "写测试", "生成测试")


def _rule_intent(query: str) -> IntentLabel:
    """关键词规则兜底（无 key / 分类失败时）。优先级：graph > bug > review > test > doc > code。"""
    q = query.lower()
    if any(h in q for h in _GRAPH_HINTS) and any(h in q for h in _CODE_HINTS):
        return "graph"
    if any(h in q for h in _BUG_HINTS):
        return "bug"
    if any(h in q for h in _REVIEW_HINTS):
        return "review"
    if any(h in q for h in _TEST_HINTS):
        return "test"
    if any(h in q for h in _DOC_HINTS):
        return "doc"
    if any(h in q for h in _CODE_HINTS):
        return "code"
    return "code"  # 代码库产品默认


async def classify_intent(query: str) -> IntentLabel:
    """意图分类：LLM 结构化输出；失败/未配置 → 规则兜底。"""
    if not configured():
        return _rule_intent(query)
    try:
        structured = get_chat_model().with_structured_output(IntentSchema)
        result = await structured.ainvoke([
            {"role": "system", "content": _INTENT_SYS},
            {"role": "user", "content": query},
        ])
        return result.intent
    except Exception:  # noqa: BLE001
        return _rule_intent(query)


# ---- token → SSE 回调：自动 Agent 作答轮逐 token 推 custom 事件 ----


class TokenSSEHandler(BaseCallbackHandler):
    """捕获 ChatModel 流式 token，经 get_stream_writer 推 SSE ``token`` 事件。

    工具决策轮模型不发 content token，故天然只在最终作答轮推送。在图运行上下文外（如单测）
    get_stream_writer 不可用 → 静默跳过，绝不抛。
    """

    def on_llm_new_token(self, token: str, **kwargs) -> None:  # noqa: ARG002
        if not token:
            return
        try:
            from langgraph.config import get_stream_writer
            get_stream_writer()({"event": "token", "data": {"content": token}})
        except Exception:  # noqa: BLE001
            pass  # 非图上下文 / 无 writer → 跳过
