"""统一 LLM 客户端：OpenAI 兼容（DeepSeek / Qwen / 硅基流动 / OpenAI）。

直接用 httpx 流式调用 /chat/completions，零额外依赖；Phase 7 Agent 层再引入
langchain-openai 做工具调用与结构化输出。无 API Key 时 configured=False，上层优雅降级。
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.clients.model_adapter import resolve_endpoint


class LLMClient:
    def __init__(self, *, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None) -> None:
        # M44：默认端点经 ModelAdapter 解析（reasoning 档；MODEL_ROUTES 空 = 旧 llm_*
        # 逐字节一致）。走 model_adapter 纯函数而非 model_router——防循环导入
        # （model_router → llm_client）。显式传参优先（legacy_client_for 即经此注入档位）。
        ep = resolve_endpoint("reasoning")
        self.base_url = (base_url or ep.base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else ep.api_key
        self.model = model or ep.model

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def stream_tokens(
        self, messages: list[dict], *, temperature: float = 0.3, max_tokens: int = 2048,
        usage_out: dict | None = None,
    ) -> AsyncIterator[str]:
        """流式产出 token；未配置 Key 时直接返回（上层负责降级提示）。

        M41：请求 ``stream_options.include_usage``，final chunk 的 ``usage`` 写入
        ``usage_out``（可选出参，provider 不返则不填；迭代结束后读取）。
        """
        if not self.configured:
            return
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=None, write=30, pool=10)) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise RuntimeError(f"LLM HTTP {resp.status_code}: {body.decode('utf-8','replace')[:300]}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    u = obj.get("usage")
                    if isinstance(u, dict) and usage_out is not None:
                        usage_out.clear()
                        usage_out.update(u)
                    try:
                        delta = obj["choices"][0].get("delta", {})
                    except (KeyError, IndexError):
                        continue
                    tok = delta.get("content")
                    if tok:
                        yield tok

    async def chat_meta(self, messages: list[dict], **kw) -> tuple[str, dict | None]:
        """非流式：聚合返回 (全文, usage|None)。eval/rewrite 等需要 token 真值的场景用。"""
        chunks: list[str] = []
        usage: dict = {}
        async for t in self.stream_tokens(messages, usage_out=usage, **kw):
            chunks.append(t)
        return "".join(chunks), (dict(usage) if usage else None)

    async def chat(self, messages: list[dict], **kw) -> str:
        """非流式：聚合 token 返回全文（行为不变，内部走 chat_meta）。"""
        text, _ = await self.chat_meta(messages, **kw)
        return text


llm = LLMClient()
