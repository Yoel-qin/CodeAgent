"""LLM 连通性自检：验证 .env 里的 LLM_API_KEY / BASE_URL / MODEL 是否可用。

用法: uv run python scripts/llm_ping.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.clients.llm_client import LLMClient  # noqa: E402
from app.core.config import settings  # noqa: E402


async def main() -> int:
    print(f"provider : {settings.llm_provider}")
    print(f"base_url : {settings.llm_base_url}")
    print(f"model    : {settings.llm_model}")
    masked = (settings.llm_api_key[:6] + "..." + settings.llm_api_key[-4:]) if settings.llm_api_key else "(empty)"
    print(f"api_key  : {masked}")
    print(f"configured: {bool(settings.llm_api_key)}")
    if not settings.llm_api_key:
        print("\n❌ 未配置 LLM_API_KEY。请在项目根 .env 设置（DeepSeek / 阿里云百炼 / 硅基流动）。")
        return 1

    client = LLMClient()
    print("\n发送测试请求（流式）…")
    tokens: list[str] = []
    try:
        async for tok in client.stream_tokens(
            [{"role": "user", "content": "用一句话介绍 Apache RocketMQ 的事务消息。"}],
            temperature=0.2, max_tokens=128,
        ):
            tokens.append(tok)
            sys.stdout.write(tok)
            sys.stdout.flush()
    except Exception as e:  # noqa: BLE001
        print(f"\n❌ 调用失败：{type(e).__name__}: {e}")
        return 2
    print(f"\n\n✅ 成功，共 {len(tokens)} 个 token 片段。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
