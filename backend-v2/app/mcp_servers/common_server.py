"""common-mcp server：3 个轻量通用工具（时间 / 澄清 / 反馈）。

- 补齐 spec §3.2 工具清单的「轻量」三件，无仓库入参、无 LLM 依赖
- submit_feedback 走 sync Session（沿 CLI 铁律；graph_query._get_engine 同款
  模块级惰性单例）INSERT feedback 表——DB 异常折成 {"error": ...} 不抛
- _run 两级捕获（Timeout / broad-except）逐字沿 code_server 模式，永不炸穿协议层
- 运行：python -m app.mcp_servers.common_server            → streamable-http :8113/mcp
        python -m app.mcp_servers.common_server --stdio    → stdio（测试/同机 sidecar）
"""
import argparse
import asyncio
import sys
import threading
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.chat import Feedback

mcp = FastMCP("common-mcp", host=settings.mcp_host, port=settings.mcp_common_port)

TIMEOUT = 5
VALID_RATINGS = {"HELPFUL", "NOT_HELPFUL"}

# ── PG 模块级惰性单例（同步，沿 graph_query._get_engine 模式） ──────────────
_engine = None
_lock = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                _engine = create_engine(
                    settings.postgres_dsn_sync,
                    pool_size=2,
                    max_overflow=2,
                    pool_pre_ping=True,
                )
    return _engine


async def _run(timeout: float, fn, *args) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
    except TimeoutError:
        return {"error": f"timeout after {timeout}s"}
    except Exception as e:  # noqa: BLE001 —— 永不炸穿 MCP 协议层
        return {"error": f"internal error: {type(e).__name__}: {e}"}


def _now_impl() -> dict:
    return {"iso": datetime.now().isoformat(timespec="seconds"), "tz": "local"}


def _clarify_impl(text: str) -> dict:
    return {
        "question": f"关于「{text[:80]}」，能否补充：1) 涉及的类名/模块名；"
        "2) 期望的答案形式（代码位置/调用链/文档章节）？"
    }


def _submit_feedback_impl(rating: str, comment: str, message_id: int | None) -> dict:
    with Session(_get_engine()) as session:
        row = Feedback(rating=rating, comment=comment, message_id=message_id)
        session.add(row)
        session.flush()  # 先取应用内可读的 id，再提交
        fid = row.id
        session.commit()
        return {"ok": True, "id": fid}


@mcp.tool()
async def get_current_time() -> dict:
    """当前本地时间（ISO 8601 秒精度 + tz 标记）。供时间线/日志类问答取「现在」。"""
    return await _run(TIMEOUT, _now_impl)


@mcp.tool()
async def clarify_question(text: str) -> dict:
    """把模糊提问转成结构化澄清请求（纯模板，零 LLM）。text 截前 80 字符。"""
    return await _run(TIMEOUT, _clarify_impl, text)


@mcp.tool()
async def submit_feedback(rating: str, comment: str = "", message_id: int | None = None) -> dict:
    """记录用户对回答的反馈并落库。rating 仅接受 HELPFUL / NOT_HELPFUL；
    message_id 为可选的关联消息 id（无外键）。返回 {"ok": true, "id": <反馈id>}。"""
    if rating not in VALID_RATINGS:
        return {"error": f"invalid rating: {rating}（仅接受 HELPFUL / NOT_HELPFUL）"}
    return await _run(TIMEOUT, _submit_feedback_impl, rating, comment, message_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true", help="stdio 传输（默认 streamable-http）")
    args = parser.parse_args()
    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
