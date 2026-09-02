"""MCP 工具加载/分区/wrap 层（Plan 3 Task 5）——ReAct 骨架（Task 8）的工具地基。

三层职责：

1. **加载** `load_tools()`：三个 **独立** 的 ``MultiServerMCPClient``（code :8110 /
   doc :8111 / graph :8112，streamable-http），逐 client ``await get_tools()`` 独立
   try/except——一个 server 挂只把该组工具置 ``[]`` + log warning，不影响其余
   （兑现降级链「code-mcp 挂 → 仅文档问答」）。adapters 0.3.x 的 client **不支持
   ``async with``**（``__aenter__`` 直接 raise），构造后直接 ``await get_tools()``；
   连接配置留在工具闭包里、每次工具调用新建 session，故 client 本体用完即弃、
   无需关闭句柄，shutdown 清理 = `reset_tools()` 清缓存。
2. **分区** `get_code_tools()`（code 5 + graph 4）/ `get_doc_tools()`（doc 5）。
3. **wrap** `wrap_tool()`：用 ``StructuredTool``（name/description/args_schema 同原、
   coroutine=wrapped）重造每个工具，wrapped 在透传之外做三件事——
   同 (tool, args) 3 次循环检测、耗时计步（``tracker.steps``）、从工具观察的
   JSON 里提取 citation（``tracker.citations``，去重 + 截 20）。
"""
import json
import time

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from loguru import logger

from app.core.config import settings

# ── 常量 ──────────────────────────────────────────────────────────────────

SERVER_NAMES = ("code", "doc", "graph")

MAX_CITATIONS = 20

LOOP_MESSAGE = '{"error": "loop detected: same tool+args 3 times"}'

# 模块级缓存：server 名 → 已加载工具（挂掉的 server 保持 []）
_TOOLS: dict[str, list[BaseTool]] = {name: [] for name in SERVER_NAMES}

# citation 去重键（Global Constraints 冻结）：code=(file_path, start_line)、doc=(doc_id, section)


def _default_transports() -> dict[str, dict]:
    """按 settings 拼三 server 的 streamable-http 连接配置（生产路径）。"""
    return {
        "code": {"url": f"http://{settings.mcp_host}:{settings.mcp_code_port}/mcp",
                 "transport": "streamable_http"},
        "doc": {"url": f"http://{settings.mcp_host}:{settings.mcp_doc_port}/mcp",
                "transport": "streamable_http"},
        "graph": {"url": f"http://{settings.mcp_host}:{settings.mcp_graph_port}/mcp",
                  "transport": "streamable_http"},
    }


async def load_tools(transports: dict[str, dict] | None = None) -> None:
    """加载并缓存三 MCP server 的工具；`transports` 供测试注入 stdio 配置。

    每个 server 独立 try/except：加载失败 → 该组工具置 ``[]`` + log warning，
    其余 server 不受影响；未知 server 名跳过并告警。整体失败不抛（lifespan
    侧仍有兜底 try/except）。
    """
    cfg = dict(transports) if transports is not None else _default_transports()
    for name, conn in cfg.items():
        if name not in SERVER_NAMES:
            logger.warning("tools_loader: 未知 server 名 {!r}（可选 {}），跳过", name, SERVER_NAMES)
            continue
        try:
            client = MultiServerMCPClient({name: conn})
            tools = await client.get_tools()
        except Exception as e:  # noqa: BLE001 —— 单 server 挂不拖垮其余（降级链地基）
            logger.warning("tools_loader: {}-mcp 工具加载失败，该组置空降级: {}", name, e)
            tools = []
        _TOOLS[name] = list(tools)
        logger.info("tools_loader: {}-mcp 加载 {} 个工具{}", name, len(tools),
                    "" if tools else "（降级：该 server 不可用）")


def reset_tools() -> None:
    """清空工具缓存（shutdown 清理 / 测试隔离）。"""
    for name in SERVER_NAMES:
        _TOOLS[name] = []


def get_code_tools() -> list[BaseTool]:
    """代码侧工具 = code-mcp 5 + graph-mcp 4。"""
    return _TOOLS["code"] + _TOOLS["graph"]


def get_doc_tools() -> list[BaseTool]:
    """文档侧工具 = doc-mcp 5。"""
    return _TOOLS["doc"]


def tools_ready() -> bool:
    """至少一个 server 的工具已加载即视为 ready（允许部分降级）。"""
    return any(_TOOLS.values())


# ── wrap：计步 + 循环检测 + citation 提取 ─────────────────────────────────


class ToolCallTracker:
    """每请求 new 一个：ReAct 循环里 wrap_tool 持续写入，Task 8 骨架只读消费。

    - ``steps``：``[{"tool", "args", "n", "duration_ms"}]``（n 为 1 起步序号）
    - ``citations``：从工具观察提取的引用（跨调用去重，合计截 :data:`MAX_CITATIONS`）
    - ``looped``：触发循环检测的工具名（第 3 次同 (tool, args) 时记一次）
    """

    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.citations: list[dict] = []
        self.looped: list[str] = []
        self._counts: dict[tuple[str, str], int] = {}
        self._seen: set[tuple] = set()

    @staticmethod
    def _key(c: dict) -> tuple:
        if c.get("kind") == "code":
            return ("code", c.get("file_path"), c.get("start_line"))
        return ("doc", c.get("doc_id"), c.get("section"))

    def add_citations(self, items: list[dict]) -> None:
        """追加引用：按 (file_path, start_line) / (doc_id, section) 去重，合计截 20。"""
        for c in items:
            key = self._key(c)
            if key in self._seen or len(self.citations) >= MAX_CITATIONS:
                continue
            self._seen.add(key)
            self.citations.append(c)


def _code(file_path: str, line: int, label: str, end: int | None = None) -> dict:
    return {"kind": "code", "file_path": file_path, "start_line": line,
            "end_line": end if end is not None else line, "label": label}


def _doc(doc_name: str, anchor: str, title: str) -> dict:
    return {"kind": "doc", "doc_id": doc_name, "section": anchor, "label": f"{doc_name}#{title}"}


def _extract_citations(tool_name: str, args: dict, result: dict) -> list[dict]:
    """按工具名分派，从工具观察的 JSON 里提取 citation（形状与 Plan 1/2 冻结契约一致）。

    提取不到（文件级结果 / error 结果 / 未知工具）→ ``[]``。
    """
    if not isinstance(result, dict) or "error" in result:
        return []
    if tool_name == "grep_code":
        out = []
        for m in result.get("matches") or []:
            if m.get("file") and m.get("line"):
                out.append(_code(m["file"], m["line"], f"{m['file']}:{m['line']}"))
        return out
    if tool_name == "read_file":
        file_path = args.get("file_path")
        if not file_path:
            return []
        start = result.get("start_line") or args.get("start_line") or 1
        end = result.get("end_line") or (args.get("end_line") or start + 99)
        return [_code(file_path, start, f"{file_path}:{start}-{end}", end=end)]
    if tool_name == "find_symbol":
        out = []
        symbol = args.get("symbol_name") or ""
        for loc in result.get("locations") or []:
            if not (loc.get("file") and loc.get("line")):
                continue
            label = f"{loc['file']}#{symbol}" if loc.get("kind") == "method" else f"{loc['file']}:{loc['line']}"
            out.append(_code(loc["file"], loc["line"], label))
        return out
    if tool_name in ("get_callees", "get_callers"):
        out = []
        for e in result.get("edges") or []:
            if e.get("file") and e.get("line"):
                out.append(_code(e["file"], e["line"], f"{e['file']}:{e['line']}"))
        return out
    if tool_name in ("doc_semantic_search", "doc_keyword_search", "doc_hybrid_search"):
        out = []
        for r in result.get("results") or []:
            if r.get("doc_name") and r.get("anchor"):
                out.append(_doc(r["doc_name"], r["anchor"], r.get("title") or ""))
        return out
    if tool_name == "read_doc_section":
        if result.get("doc_name") and result.get("anchor"):
            return [_doc(result["doc_name"], result["anchor"], result.get("title") or "")]
        return []
    if tool_name == "get_doc_toc":
        out = []
        for t in result.get("toc") or []:
            if t.get("doc_name") and t.get("anchor"):
                out.append(_doc(t["doc_name"], t["anchor"], t.get("title") or ""))
        return out
    return []  # glob_files / list_directory / get_module_deps / code_metrics / 未知工具


def _result_text(result: object) -> str:
    """把工具调用结果归一成字符串观测。

    adapters 0.3.x 的 MCP 工具 ``ainvoke`` 返回 **content-block 列表**
    （``[{"type": "text", "text": ...}]``，response_format=content_and_artifact），
    普通 ``StructuredTool`` 返回 str——两种都要兜住。
    """
    if isinstance(result, str):
        return result
    if isinstance(result, tuple):  # (content, artifact)
        return _result_text(result[0])
    if isinstance(result, list):
        parts = [b.get("text", "") for b in result if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return "" if result is None else str(result)


def wrap_tool(tool: BaseTool, tracker: ToolCallTracker) -> BaseTool:
    """给工具套上 计步/循环检测/citation 提取，返回新 ``StructuredTool``（原工具不动）。

    name/description/args_schema 同原（Task 8 的 ReAct 骨架按这三个字段向 LLM 注册）；
    wrapped 顺序：① 循环检测（第 3 次同 (name, args) → 返回 LOOP_MESSAGE，不执行）
    ② 计时执行 ③ 从结果 JSON 提取 citation ④ 追加 step ⑤ 原样返回结果字符串。
    """
    async def _wrapped(**kwargs):
        key = (tool.name, json.dumps(kwargs, sort_keys=True, ensure_ascii=False, default=str))
        n_hits = tracker._counts.get(key, 0) + 1
        tracker._counts[key] = n_hits
        if n_hits >= 3:
            if n_hits == 3:  # 只在触发的第 3 次记一次，后续同参仍拦截但不重复膨胀 looped
                tracker.looped.append(tool.name)
            return LOOP_MESSAGE
        started = time.perf_counter()
        result = await tool.ainvoke(kwargs)
        duration_ms = (time.perf_counter() - started) * 1000
        text = _result_text(result)
        try:
            parsed = json.loads(text)
        except ValueError:  # json.JSONDecodeError ⊂ ValueError
            parsed = None
        if isinstance(parsed, dict):
            tracker.add_citations(_extract_citations(tool.name, kwargs, parsed))
        tracker.steps.append({"tool": tool.name, "args": dict(kwargs), "n": len(tracker.steps) + 1,
                              "duration_ms": round(duration_ms, 1)})
        return text

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        coroutine=_wrapped,
    )
