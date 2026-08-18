"""LLM 连通性自检：验证 .env 里的 LLM_API_KEY / BASE_URL / MODEL 是否可用。

用法: uv run python scripts/llm_ping.py [--tier routing|extraction|reasoning]
M44：--tier 经 ModelRouter 打对应档位端点（默认 reasoning = 旧行为）——切 vLLM 后
逐档实测（CI 不跑，需真实端点）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.clients.model_router import endpoint_for, legacy_client_for  # noqa: E402
from app.core.config import settings  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="LLM 连通性自检（支持三档端点路由）")
    p.add_argument("--tier", choices=["routing", "extraction", "reasoning"],
                   default="reasoning",
                   help="M44 档位（默认 reasoning = 旧行为）")
    return p


async def main(tier: str = "reasoning") -> int:
    ep = endpoint_for(tier)
    print(f"tier    : {tier}")
    print(f"provider: {settings.llm_provider}")
    print(f"base_url: {ep.base_url}")
    print(f"model   : {ep.model}")
    masked = (ep.api_key[:6] + "..." + ep.api_key[-4:]) if ep.api_key else "(empty)"
    print(f"api_key : {masked}")
    print(f"configured: {bool(ep.api_key)}")
    if not ep.api_key:
        print("\n❌ 该档位未解析到 api_key（MODEL_ROUTES 未覆盖且 LLM_API_KEY 为空）。")
        return 1

    client = legacy_client_for(tier)
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
    raise SystemExit(asyncio.run(main(**vars(build_parser().parse_args()))))
