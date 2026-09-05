"""视觉描述客户端：SiliconFlow 视觉模型 /chat/completions（默认 PaddleOCR-VL-1.5）。

真实库验证 T4-1 follow-up：图片型 docx 的截图对检索不可见 → ingest 期逐图生成中文
描述。软失败契约镜像 :mod:`app.clients.embedding_client`：任何失败 → ``None``，绝不
抛（描述是增强非解析义务）；``vision_base_url``/``vision_api_key`` 空值回落
``embedding_*``（同服务商字段级回落，对称 MODEL_ROUTES）。同步 httpx——ingest CLI
天然串行，逐图一次往返。
"""
from __future__ import annotations

import base64

import httpx

from app.core.config import settings

__all__ = ["describe_image"]

_PROMPT = (
    "识别并转录这张图片中的全部文字内容，按原始结构组织（标题、列表、表格、界面控件"
    "与配置项）；若是软件界面截图，说明这是哪个界面/对话框及其中可见的操作项。用中文输出。"
)


def describe_image(image_bytes: bytes, *, ext: str = "png",
                   timeout: float = 60.0) -> str | None:
    """单图 → 中文视觉描述；开关 off / 无 key / 空字节 / 任何异常 / 空回复 → ``None``。"""
    base_url = settings.vision_base_url or settings.embedding_base_url
    api_key = settings.vision_api_key or settings.embedding_api_key
    if not settings.vision_desc_enabled or not api_key or not image_bytes:
        return None
    b64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        r = httpx.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": settings.vision_model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
                ]}],
                "max_tokens": 1024,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        text = (r.json()["choices"][0]["message"]["content"] or "").strip()
        return text or None
    except Exception:
        return None
