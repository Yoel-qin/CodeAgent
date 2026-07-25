"""统一 LLM 客户端：OpenAI 兼容（DeepSeek / Qwen / 硅基流动 / OpenAI）。

直接用 httpx 流式调用 /chat/completions，零额外依赖；Phase 7 Agent 层再引入
langchain-openai 做工具调用与结构化输出。无 API Key 时 configured=False，上层优雅降级。
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.core.config import settings


class LLMClient:
    def __init__(self, *, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def stream_tokens(
        self, messages: list[dict], *, temperature: float = 0.3, max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """流式产出 token；未配置 Key 时直接返回（上层负责降级提示）。"""
        if not self.configured:
            return
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
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
                    try:
                        delta = obj["choices"][0].get("delta", {})
                    except (KeyError, IndexError):
                        continue
                    tok = delta.get("content")
                    if tok:
                        yield tok

    async def chat(self, messages: list[dict], **kw) -> str:
        """非流式：聚合 token 返回全文。"""
        chunks: list[str] = []
        async for t in self.stream_tokens(messages, **kw):
            chunks.append(t)
        return "".join(chunks)


llm = LLMClient()
