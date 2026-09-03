"""图节点实现（Plan 3）：本任务只含 Task 7 的 ``retrieve_node`` / ``clarify_node`` 兜底对。

- **retrieve_node**（``route == "retrieve"``：None/简单事实/web 的兜底）——纯检索路径，
  **无 LLM 也能出片段**：doc 路 ``hybrid_search`` + code 路 query 提取英文标识符逐个
  ``grep_code``，两路独立 try/except（一路挂另一路照常）；doc 命中后经 ``get_doc_toc``
  建 ``(doc_name, anchor) → document_id`` 映射、逐条 ``read_doc_section`` 补正文前 500 字
  （R1 增强——hybrid 冻结结果形状无 content，缺正文则 retrieve 的 doc 命中等于零材料；
  TOC/read 任一步失败 → 软失败退回标题行，不为增强破坏降级链）；先发 ``retrieval`` 事件
  （冻结形状 ``{mode, intent, confidence, code_hits, doc_hits}``），再逐条发 ``citation``
  （形状与 Task 5 tools_loader 冻结契约一致），最后生成侧二选一——LLM 可用
  （``configured()``）→ context 拼 user 消息 ``astream`` 逐 chunk 发 token；不可用 →
  单条 token 发 ``[未配置 LLM Key，以下为检索片段]`` + 每条 ``file:line 首行内容``。
  整体再兜一层 try/except：逃逸异常发 ``[检索降级失败: {类型名}]``，永不炸。
- **clarify_node**（``route == "clarify"``：``confidence < 0.7`` 的追问兜底）——先发
  ``retrieval``（mode="clarify"，hits 恒 0），extraction 档 ``.invoke``（to_thread +
  ``wait_for`` 5s）生成一句中文追问；失败/无 key → 固定模板；追问文本按 64 字符切片
  逐个发 token（沿旧库节奏）。

两节点均返回 ``{"answer": None}``——answer 由 Task 9 streaming 适配层从 token 事件累积
（事件即数据，agent 不写图 state；旧库 adapter 同构）。Task 8 的 ReAct 降级链与
Task 9 的图装配都消费 retrieve_node。
"""
# 注意：本模块**不**加 ``from __future__ import annotations``——langgraph 按运行时注解
# 对象识别节点可注入的 ``config`` 形参，字符串化的 ``"RunnableConfig | None"`` 不在其
# 白名单 → config 被静默丢弃（configurable 里的 session/cost/top_k 全落空）+ UserWarning；
# 真注解对象 ``RunnableConfig | None == Optional[RunnableConfig]`` 才匹配（Task 9 实测）。
import asyncio
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from loguru import logger

from app.agent.callbacks import CostCallbackHandler
from app.agent.state import AgentState
from app.clients.llm import chat_model_for, configured
from app.core.config import settings
from app.core.doc_search import get_doc_toc, hybrid_search, read_doc_section
from app.core.grep import grep_code

__all__ = ["AgentState", "clarify_node", "retrieve_node"]

# ── 常量（brief 冻结值） ──────────────────────────────────────────────────

#: doc 路 top_k
_RETRIEVE_TOP_K = 8
#: code 路：query 提取英文标识符的前 N 个各跑一次 grep，每次 max_results
_CODE_TOKEN_LIMIT = 3
_GREP_MAX_RESULTS = 5
_GREP_GLOB = "**/*.java"
#: LLM context 与无 key 片段各自截取条数
_SNIPPET_LIMIT = 8
_CONTEXT_CONTENT_CHARS = 500
#: doc 正文增强（R1）：最多补 N 条命中的 read_doc_section 正文
_ENRICH_LIMIT = 5
#: clarify：extraction 档超时（秒）与追问文本切片长度
_CLARIFY_TIMEOUT_S = 5.0
_TOKEN_CHUNK_CHARS = 64

_RETRIEVE_SYSTEM = (
    "你是代码库问答助手；引用规范：提及代码处标注 `文件:行号`，"
    "引用文档处标注 `[文档名#标题]`；只依据给定材料回答"
)
_CLARIFY_SYSTEM = "把用户的模糊问题改写成一句具体的中文澄清追问，直接输出追问本身，不要解释"
_CLARIFY_FALLBACK = (
    "为了准确定位，能否补充：您关注的类名/模块，"
    "以及期望的答案形式（代码位置/调用链/文档章节）？"
)
_NO_KEY_HEADER = "[未配置 LLM Key，以下为检索片段]"


def _cost_callbacks(config: RunnableConfig | None) -> dict:
    """LLM 调用挂账：``configurable["cost"]`` 有 → 回调 dict；缺席 → ``{}``（零行为变）。

    ReAct 路由 react_base 统一挂 CostCallbackHandler；retrieve/clarify 的直连 LLM
    调用此前漏挂——不挂则这两路的调用不进预算账本（Task 10 评审遗留）。
    """
    cost = (config or {}).get("configurable", {}).get("cost")
    return {"callbacks": [CostCallbackHandler(cost)]} if cost is not None else {}


def _safe_writer():
    """``get_stream_writer()`` 失败（不在图执行上下文，如测试直调）→ no-op。

    沿旧库 ``_base._safe_writer`` 模式，差异：返回 no-op lambda 而非 ``None``，
    调用点免判空（``langgraph.config.get_stream_writer`` 在上下文外直接 raise）。
    """
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001 —— 无 writer 上下文时静默丢弃事件
        return lambda _chunk: None


# ── 小工具 ────────────────────────────────────────────────────────────────


def _first_line(text: str, limit: int = 200) -> str:
    """取第一行非空内容截断（无 key 片段的"首行内容"）。"""
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln:
            return ln[:limit]
    return ""


def _doc_citation(r: dict) -> dict | None:
    doc_name, anchor = r.get("doc_name"), r.get("anchor")
    if not (doc_name and anchor):
        return None
    return {"kind": "doc", "doc_id": doc_name, "section": anchor,
            "label": f"{doc_name}#{r.get('title') or ''}"}


def _code_citation(m: dict) -> dict | None:
    file_path, line = m.get("file"), m.get("line")
    if not (file_path and line):
        return None
    return {"kind": "code", "file_path": file_path, "start_line": line,
            "end_line": line, "label": f"{file_path}:{line}"}


def _history_messages(history: list[dict] | None) -> list:
    """``chat_messages`` 形状的 ``{role, content}`` 列表 → langchain 消息。"""
    out: list = []
    for m in history or []:
        content = (m.get("content") or "").strip() if isinstance(m, dict) else ""
        if not content:
            continue
        if m.get("role") == "assistant":
            out.append(AIMessage(content=content))
        elif m.get("role") == "user":
            out.append(HumanMessage(content=content))
    return out


def _build_context(doc_results: list[dict], code_matches: list[dict]) -> str:
    """LLM 材料：doc 段落（title+anchor+内容前 500 字）+ code 行，各截前 8 条。"""
    parts: list[str] = []
    if doc_results:
        lines = ["### 文档片段"]
        for r in doc_results[:_SNIPPET_LIMIT]:
            head = f"[{r.get('doc_name')}#{r.get('title') or ''}]（anchor {r.get('anchor')}）"
            body = (r.get("content") or "")[:_CONTEXT_CONTENT_CHARS].strip()
            lines.append(f"- {head}\n  {body}" if body else f"- {head}")
        parts.append("\n".join(lines))
    if code_matches:
        lines = ["### 代码片段"]
        for m in code_matches[:_SNIPPET_LIMIT]:
            lines.append(f"- {m.get('file')}:{m.get('line')} {(m.get('content') or '').rstrip()}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _snippet_text(doc_results: list[dict], code_matches: list[dict]) -> str:
    """无 key 片段摘要：每条 ``file:line 首行内容``（code+doc 各前 8）。"""
    lines = [_NO_KEY_HEADER]
    for m in code_matches[:_SNIPPET_LIMIT]:
        lines.append(f"{m.get('file')}:{m.get('line')} {_first_line(m.get('content') or '')}".rstrip())
    for r in doc_results[:_SNIPPET_LIMIT]:
        head = f"[{r.get('doc_name')}#{r.get('title') or ''}]"
        lines.append(f"{head} {_first_line(r.get('content') or '')}".rstrip())
    return "\n".join(lines)


async def _enrich_doc_content(repo: str, doc_results: list[dict]) -> None:
    """给 doc 命中补 PG 正文（前 500 字）——hybrid 冻结结果形状无 content（R1 增强）。

    ``get_doc_toc`` 建 ``(doc_name, anchor) → document_id`` 映射后，前 N 条逐条
    ``read_doc_section`` 取正文原地写入 ``r["content"]``（覆盖 hybrid 自带同名键）；
    TOC 挂 → 整体跳过退回标题行、单条 read 失败/未命中 → 跳过该条，均不抛
    （不为增强破坏降级链）。同步 core 调用经 ``asyncio.to_thread`` **只传位置参数**。
    """
    if not doc_results:
        return
    try:
        toc = await asyncio.to_thread(get_doc_toc, repo)
        rows = (toc or {}).get("toc") or []
        ids = {(r.get("doc_name"), r.get("anchor")): r.get("document_id") for r in rows}
    except Exception as e:  # noqa: BLE001 —— TOC 挂 → 退回标题行
        logger.warning("retrieve_node: doc TOC 失败，正文增强跳过: {}", e)
        return
    for r in doc_results[:_ENRICH_LIMIT]:
        doc_id = ids.get((r.get("doc_name"), r.get("anchor")))
        if not doc_id:
            continue
        try:
            sec = await asyncio.to_thread(read_doc_section, repo, doc_id, r.get("anchor"))
            content = ((sec or {}).get("content") or "")[:_CONTEXT_CONTENT_CHARS].strip()
            if content:
                r["content"] = content
        except Exception as e:  # noqa: BLE001 —— 单条失败跳过
            logger.warning("retrieve_node: read_doc_section {}#{} 失败跳过: {}",
                           r.get("doc_name"), r.get("anchor"), e)


# ── retrieve：检索兜底（无 LLM 也能出片段） ────────────────────────────────


async def _recall(state: AgentState, repo: str, query: str) -> tuple[list[dict], list[dict]]:
    """doc 路 hybrid + code 路 grep；两路独立 try/except，一路挂另一路照常。

    ``hybrid_search`` / ``grep_code`` 均同步——``asyncio.to_thread`` **只传位置参数**
    （keyword-only 经 to_thread 会 TypeError 且被本层 try 静默吞掉，见旧库坑）。
    """
    doc_results: list[dict] = []
    try:
        res = await asyncio.to_thread(hybrid_search, repo, query, _RETRIEVE_TOP_K, None)
        doc_results = (res or {}).get("results") or []
    except Exception as e:  # noqa: BLE001 —— doc 路软失败降级为空
        logger.warning("retrieve_node: doc 路失败降级为空: {}", e)

    code_matches: list[dict] = []
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)[:_CODE_TOKEN_LIMIT]
    for tok in toks:
        try:
            res = await asyncio.to_thread(
                grep_code, settings.repos_root, repo, tok, _GREP_GLOB, True, _GREP_MAX_RESULTS
            )
            code_matches.extend((res or {}).get("matches") or [])
        except Exception as e:  # noqa: BLE001 —— code 路软失败跳过该词
            logger.warning("retrieve_node: code 路 {!r} 失败跳过: {}", tok, e)
    return doc_results, code_matches


async def retrieve_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """检索兜底节点：retrieval → citations → LLM 流式生成（或无 key 片段）。

    M7 起可选 trace（``configurable["trace"]``）：整体 try 内主流程外包一个
    ``retrieval`` span（attrs 携 mode）；异常降级 → ``status="error"``；缺席零行为变更。
    """
    w = _safe_writer()
    trace = (config or {}).get("configurable", {}).get("trace")
    sid = None
    try:
        if trace is not None:
            sid = trace.start("retrieval", "retrieve", attrs={"mode": "retrieve"})
        query = state.get("query", "") or ""
        repo = state.get("repo") or settings.default_repo
        doc_results, code_matches = await _recall(state, repo, query)
        await _enrich_doc_content(repo, doc_results)
        w({"event": "retrieval", "data": {
            "mode": "retrieve",
            "intent": state.get("intent", ""),
            "confidence": state.get("confidence", 0.0),
            "code_hits": len(code_matches),
            "doc_hits": len(doc_results),
        }})
        for r in doc_results:
            if c := _doc_citation(r):
                w({"event": "citation", "data": c})
        for m in code_matches:
            if c := _code_citation(m):
                w({"event": "citation", "data": c})

        if configured():
            context = _build_context(doc_results, code_matches)
            messages = [
                SystemMessage(content=_RETRIEVE_SYSTEM),
                *_history_messages(state.get("history")),
                HumanMessage(content=f"{query}\n\n【检索材料】\n{context}" if context else query),
            ]
            async for chunk in chat_model_for("reasoning").astream(messages, config=_cost_callbacks(config)):
                w({"event": "token", "data": {"content": chunk.content}})
        else:
            w({"event": "token", "data": {"content": _snippet_text(doc_results, code_matches)}})
        if trace is not None:
            trace.end(sid)
    except Exception as e:  # noqa: BLE001 —— 兜底的兜底，请求永不破
        logger.warning("retrieve_node: 整体降级: {}", e)
        if sid is not None:
            trace.end(sid, status="error", error=type(e).__name__)
        w({"event": "token", "data": {"content": f"[检索降级失败: {type(e).__name__}]"}})
    return {"answer": None}


# ── clarify：澄清追问（confidence < 0.7 的兜底） ──────────────────────────


async def clarify_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """澄清追问节点：retrieval(mode=clarify) → 追问文本 64 字符切片逐个发 token。

    extraction 档 ``.invoke``（同步调用经 ``to_thread`` + ``wait_for`` 5s）；失败/超时/
    无 key → 固定模板追问，永不炸。M7 起可选 trace：主流程外包一个 ``retrieval``
    span（attrs 携 mode）；缺席零行为变更。
    """
    w = _safe_writer()
    trace = (config or {}).get("configurable", {}).get("trace")
    sid = trace.start("retrieval", "clarify", attrs={"mode": "clarify"}) if trace is not None else None
    try:
        w({"event": "retrieval", "data": {
            "mode": "clarify",
            "intent": state.get("intent", ""),
            "confidence": state.get("confidence", 0.0),
            "code_hits": 0,
            "doc_hits": 0,
        }})
        text: str | None = None
        if configured():
            try:
                model = chat_model_for("extraction")
                messages = [
                    SystemMessage(content=_CLARIFY_SYSTEM),
                    HumanMessage(content=state.get("query", "") or ""),
                ]
                resp = await asyncio.wait_for(
                    asyncio.to_thread(model.invoke, messages, _cost_callbacks(config)),
                    _CLARIFY_TIMEOUT_S,
                )
                text = (getattr(resp, "content", "") or "").strip() or None
            except Exception as e:  # noqa: BLE001 —— 无 key/超时/异常一律模板
                logger.warning("clarify_node: extraction 档追问失败，模板兜底: {}", e)
        text = text or _CLARIFY_FALLBACK
        for i in range(0, len(text), _TOKEN_CHUNK_CHARS):
            w({"event": "token", "data": {"content": text[i:i + _TOKEN_CHUNK_CHARS]}})
    finally:
        if sid is not None:
            trace.end(sid)
    return {"answer": None}
