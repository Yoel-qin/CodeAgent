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
from app.clients.model_router import endpoint_for
from app.core.config import settings
from app.domain_packs.models import DomainPack

# 意图标签：router 据此条件路由（code → 代码理解；doc → 文档问答；graph → 变更影响；
# bug → 缺陷诊断；review → 代码审查；test → 测试生成；mixed/chitchat → retrieve 兜底；
# M37 领域意图：trace=调用链路梳理，diagnose=领域诊断决策树，tune=性能调优规则）
IntentLabel = Literal["code", "doc", "graph", "bug", "review", "test", "web",
                      "trace", "diagnose", "tune", "mixed", "chitchat"]

# 无领域包时 LLM 结构化输出的合法标签（排除 trace/diagnose/tune 领域意图）。
# IntentSchemaBase 用于 with_structured_output，确保 LLM 不可能产出领域标签。
_IntentLabelBase = Literal["code", "doc", "graph", "bug", "review", "test", "web",
                            "mixed", "chitchat"]

_TIER_MODELS: dict[tuple[str, str, str, str], ChatOpenAI] = {}


def model_for(purpose: str = "reasoning") -> ChatOpenAI:
    """M44 端点路由：经 ModelRouter 取 (base_url, api_key, model) 构造 ChatOpenAI。

    MODEL_ROUTES 空 = 三档回落既有 llm_*（与 M42 行为逐字节一致）。api_key 合成
    ``endpoint.api_key or "EMPTY"``——哑钥匙防 openai>=1 构造期校验抛（仅当该档
    显式指向端点且全局无 key 时生效；vLLM 接受任意值）。缓存 key 为
    (purpose, 端点三元组)，同档换端点换实例；调用点签名与 M42 完全一致。
    """
    ep = endpoint_for(purpose)
    key = (purpose, ep.base_url, ep.api_key, ep.model)
    if key not in _TIER_MODELS:
        _TIER_MODELS[key] = ChatOpenAI(
            model=ep.model,
            api_key=ep.api_key or "EMPTY",
            base_url=ep.base_url,
            streaming=True,
            temperature=0.3,
        )
    return _TIER_MODELS[key]


def get_chat_model() -> ChatOpenAI:
    """惰性单例（兼容别名 = model_for("reasoning")，既有调用点零改）。"""
    return model_for("reasoning")


def configured() -> bool:
    """是否配置了 LLM key（复用 legacy 客户端的判定）。"""
    return bool(legacy_llm.configured)


# ---- 意图分类（with_structured_output + 规则兜底）----


class IntentSchema(BaseModel):
    """用户提问意图 + 是否需多 Agent 协作（12 标签，含领域意图 trace/diagnose/tune）。

    pack 激活时由 ``with_structured_output(IntentSchema)`` 使用。
    """

    intent: IntentLabel = Field(description="用户提问的意图类别")
    needs_collab: bool = Field(
        default=False,
        description="是否需多 Agent 协作（复杂诊断：多阶段推理/排查根因/消息堆积/死锁/泄漏等）",
    )


class _IntentSchemaBase(IntentSchema):
    """无领域包时 LLM 结构化输出用的 schema（9 标签，intent 字段 narrow 到 _IntentLabelBase）。

    pydantic v2 子类合法 narrowing——with_structured_output 发布的 JSON schema enum
    只含 _IntentLabelBase 的 9 个标签，LLM 结构化输出不可能返回 trace/diagnose/tune。
    实例 isinstance(IntentSchema)，query_analysis 消费不变。
    """

    intent: _IntentLabelBase = Field(description="用户提问的意图类别")


_INTENT_SYS = (
    "你是意图分类器。判断用户在 Java 代码知识库中的提问意图，只输出一个类别：\n"
    "code=理解方法/类/逻辑/调用关系/实现；doc=查文档/配置/用法说明；"
    "graph=模块依赖/调用结构/影响范围；bug=排查报错/异常/崩溃/为何失败/根因诊断；"
    "review=代码审查/质量评估/潜在问题/改进建议/重构；"
    "test=生成测试/单元测试/JUnit/测试用例/测试代码；"
    "mixed=同时涉及代码与文档；chitchat=寒暄/与代码库无关。\n"
    "web=需要联网/知识库之外的信息（最新资讯、官方文档、第三方库用法、外部概念）；"
    "默认偏向 code（这是一个代码知识库）。\n"
    "needs_collab：当问题是复杂诊断（需多阶段推理：先提假设再回代码验证、排查根因/性能问题/"
    "消息堆积/死锁/泄漏/多代码段关联分析）时为 true；简单单点查询为 false。"
)

_INTENT_SYS_DOMAIN = _INTENT_SYS + (
    "\n领域意图（仅当激活领域包时使用）：\n"
    "trace=梳理某场景的完整方法调用链路/消息流程（如「发送链路」「消费流程」「完整调用路径」）；"
    "diagnose=按领域诊断决策树排查中间件故障症状（如「消息堆积」「消息丢失」「rebalance」「消费不下」）；"
    "tune=按调优规则给配置/参数性能建议（如「提高吞吐」「降低延迟」「调优」「高并发参数」）。"
    "这些领域意图优先于通用的 code/bug/review——当查询明显是上述领域场景时选领域标签。"
)

# M37 领域意图强信号（仅 pack_active=True 时启用；刻意选消息中间件场景短语，
# 区别于通用 _BUG_HINTS 报错词与 _REVIEW_HINTS 审查词）。
_TRACE_HINTS = ("链路", "完整调用", "消息流程", "发送流程", "消费流程", "调用流程",
                "trace 链路", "完整路径")
_DIAGNOSE_HINTS = ("消息堆积", "堆积", "消息丢失", "丢消息", "消费不下", "不消费",
                   "rebalance", "重平衡", "消息积压")
_TUNE_HINTS = ("调优", "提高吞吐", "降低延迟", "高并发参数", "性能优化", "tune",
               "吞吐量", "优化性能")

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
# 联网检索强信号（明确「搜互联网」；用高精确度短语，避免与 doc 的「文档」/code 的「最近变更」误撞）。
_WEB_HINTS = ("联网", "网上", "在线搜索", "search online", "search the web", "互联网")
# M35 协作触发信号（复杂诊断强信号）。规则兜底用：intent=mixed 或命中这些词 → needs_collab。
_COLLAB_HINTS = ("排查", "诊断", "堆积", "死锁", "泄漏", "性能劣化", "为什么", "导致", "根因")


def _rule_intent(query: str, *, pack_active: bool = False) -> IntentLabel:
    """关键词规则兜底（无 key / 分类失败时）。优先级：web > [领域，仅 pack_active] > graph > bug > review > test > doc > code。"""
    q = query.lower()
    if any(h in q for h in _WEB_HINTS):
        return "web"
    if pack_active:
        if any(h in q for h in _DIAGNOSE_HINTS):   # 领域诊断优先于通用 bug
            return "diagnose"
        if any(h in q for h in _TRACE_HINTS):
            return "trace"
        if any(h in q for h in _TUNE_HINTS):
            return "tune"
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


def _rule_needs_collab(query: str, intent: IntentLabel) -> bool:
    """协作判定的规则兜底：mixed 意图或命中复杂诊断信号词。"""
    if intent == "mixed":
        return True
    q = query.lower()
    return any(h in q for h in _COLLAB_HINTS)


async def classify_intent_and_collab(query: str,
                                     pack: DomainPack | None = None,
                                     *, collector=None, cost=None) -> IntentSchema:
    """意图分类 + 协作判定：一次结构化 LLM 调用产 IntentSchema（intent + needs_collab）；
    失败/未配置 → 规则兜底。

    pack 非空（激活领域包）→ 用领域版 system prompt（含 trace/diagnose/tune 判定）+ 规则兜底带 pack_active；
    pack=None → 现状（9 标签，不产领域 intent，逐字同 M36）。
    M41：传 collector 时经 TraceCallbackHandler 记 llm span（usage 真值优先）。
    M42：传 cost 时经 CostCallbackHandler 记预算账本。
    """
    pack_active = pack is not None
    if not configured():
        intent = _rule_intent(query, pack_active=pack_active)
        return IntentSchema(intent=intent, needs_collab=_rule_needs_collab(query, intent))
    try:
        sys_prompt = _INTENT_SYS_DOMAIN if pack_active else _INTENT_SYS
        schema = IntentSchema if pack_active else _IntentSchemaBase
        structured = model_for("routing").with_structured_output(schema)
        cbs: list = []
        if collector is not None:
            cbs.append(TraceCallbackHandler(collector))
        if cost is not None:
            cbs.append(CostCallbackHandler(cost))
        cfg: dict = {}
        if cbs:
            cfg["callbacks"] = cbs
        return await structured.ainvoke([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": query},
        ], config=cfg or None)
    except Exception:  # noqa: BLE001
        intent = _rule_intent(query, pack_active=pack_active)
        return IntentSchema(intent=intent, needs_collab=_rule_needs_collab(query, intent))


async def classify_intent(query: str) -> IntentLabel:
    """意图分类（保留旧契约：返回 IntentLabel）。委托 classify_intent_and_collab。"""
    return (await classify_intent_and_collab(query)).intent


# ---- token → SSE 回调：自动 Agent 作答轮逐 token 推 custom 事件 ----


def _usage_from_response(response) -> dict | None:
    """从 LLMResult 取 usage：llm_output.token_usage 优先 → chunk usage_metadata → None。

    模块级供 TraceCallbackHandler（M41 llm span）与 CostCallbackHandler（M42 记量）共用。
    """
    try:
        out = getattr(response, "llm_output", None) or {}
        tu = out.get("token_usage")
        if isinstance(tu, dict) and tu.get("prompt_tokens") is not None:
            return tu
        gens = getattr(response, "generations", None)
        if gens and gens[0]:
            msg = getattr(gens[0][0], "message", None)
            um = getattr(msg, "usage_metadata", None) if msg else None
            if isinstance(um, dict) and um.get("input_tokens") is not None:
                return {"prompt_tokens": um["input_tokens"],
                        "completion_tokens": um.get("output_tokens") or 0}
    except Exception:  # noqa: BLE001
        return None
    return None


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


class TraceCallbackHandler(TokenSSEHandler):
    """M41：TokenSSEHandler 的 Trace 扩展——同一 handler 兼得 token→SSE 与 llm span 记账。

    - ``on_llm_start`` 按 run_id 记 t0/parent（parent=collector 当前栈顶：ReAct
      ``astream`` 在 agent span 的 with 块内被 await，回调内联执行 → 栈顶正确）；
    - ``on_llm_end`` 结算：usage 优先 ``llm_output.token_usage`` → chunk
      ``usage_metadata`` → 估算（prompt 记 0，completion 按已见 token chars/4）；
    - 任何回调异常静默（旁观者契约，绝不抛）。

    ``emit_tokens`` 默认 False：propose/collab 等原本无 token 回调的注入点不得泄漏
    内容 token（M15「中断前不漏半句」）。仅 _base.run_scenario_agent 传 True。
    """

    def __init__(self, collector, *, emit_tokens: bool = False) -> None:
        self.collector = collector
        self.emit_tokens = emit_tokens
        self._pending: dict = {}  # run_id -> {"t0", "parent_id", "chars", "name"}

    @staticmethod
    def _name(serialized: dict) -> str:
        try:
            return serialized.get("name") or settings.llm_model
        except Exception:  # noqa: BLE001
            return settings.llm_model

    @staticmethod
    def _model(serialized: dict, kwargs: dict) -> str:  # noqa: ARG004
        """M44：llm span 记实际服务模型名（invocation_params.model 优先，缺则回落
        settings.llm_model）。任何异常静默（旁观者契约）。"""
        try:
            m = (kwargs.get("invocation_params") or {}).get("model")
            if m:
                return str(m)
        except Exception:  # noqa: BLE001
            pass
        try:
            return str(serialized.get("name") or settings.llm_model)
        except Exception:  # noqa: BLE001
            return settings.llm_model

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs) -> None:  # noqa: ARG002
        if self.collector is None:
            return
        try:
            import time
            self._pending[run_id] = {
                "t0": time.perf_counter(), "parent_id": self.collector.stack_top,
                "chars": 0, "name": self._name(serialized or {}),
                "model": self._model(serialized or {}, kwargs),
            }
        except Exception:  # noqa: BLE001
            pass

    def on_llm_new_token(self, token, **kwargs) -> None:
        try:
            run_id = kwargs.get("run_id")
            if run_id in self._pending:
                self._pending[run_id]["chars"] += len(token or "")
            # 仅 emit_tokens=True 时推送 token→SSE（默认 False，避免 propose/collab 泄漏内容）
            if self.emit_tokens:
                super().on_llm_new_token(token, **kwargs)
        except Exception:  # noqa: BLE001
            pass

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:  # noqa: ARG002
        if self.collector is None or run_id not in self._pending:
            return
        try:
            import time
            info = self._pending.pop(run_id)
            dur = (time.perf_counter() - info["t0"]) * 1000
            usage = _usage_from_response(response)
            from app.agent.trace import tokens_from_usage
            self.collector.record(
                "llm", info["name"], dur, parent_id=info["parent_id"],
                tokens=tokens_from_usage(usage, prompt_chars=0,
                                         completion_chars=info["chars"]),
                attrs={"model": info.get("model")},
            )
        except Exception:  # noqa: BLE001
            pass

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:  # noqa: ARG002
        if self.collector is None or run_id not in self._pending:
            return
        try:
            info = self._pending.pop(run_id)
            s = self.collector.start("llm", info["name"],
                                     parent_id=info["parent_id"],
                                     attrs={"model": info.get("model")})
            # 用 t0 回填 start_ms 使 end() 算出的 duration 反映真实 LLM 调用时长
            s.start_ms = round((info["t0"] - self.collector._t0) * 1000, 2)
            self.collector.end(s, error=f"{type(error).__name__}: {error}")
        except Exception:  # noqa: BLE001
            pass


class CostCallbackHandler(BaseCallbackHandler):
    """M42：把 LLM 调用次数/usage 记入 CostController（只记不抛——langchain 吞回调异常）。

    usage 取值复用 ``_usage_from_response``；拿不到 → 按 generations 文本 chars/4 估算
    并标 ``estimated=True``（prompt 记 0，与 M41 trace 估算口径一致）。
    拦截不在回调里做（抛不出去）：由 astream chunk 循环 / 显式调用点 check()。
    """

    def __init__(self, controller) -> None:
        self.controller = controller
        self._seen: set = set()

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs) -> None:  # noqa: ARG002
        try:
            self._seen.add(run_id)
            self.controller.record_call()
        except Exception:  # noqa: BLE001
            pass

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:  # noqa: ARG002
        if run_id not in self._seen:
            return
        try:
            usage = _usage_from_response(response)
            if usage:
                self.controller.record_usage(
                    prompt=usage.get("prompt_tokens") or 0,
                    completion=usage.get("completion_tokens") or 0)
                return
            text_len = 0
            for gens in (getattr(response, "generations", None) or []):
                for g in gens or []:
                    text_len += len(getattr(g, "text", "") or "")
            if text_len:
                self.controller.record_usage(completion=text_len // 4, estimated=True)
        except Exception:  # noqa: BLE001
            pass
