"""CodeRAG 本地模型服务：CodeBERT 代码嵌入（GPU 优先，CPU 回退）。

框架二（embedding_strategy=dual）的代码侧编码器——为代码/注释提供 768d 语义向量。
CodeBERT 不是 SentenceTransformer 模型（无内置 pooling 层），故用 transformers 直接加载：
  AutoTokenizer + AutoModel → last_hidden_state 做 attention-mask 感知 mean-pool + L2 归一化。

端点：
  GET  /health
  POST /embeddings  {"texts": [...]} -> {"embeddings": [[...]], "dim": 768}

运行：
  cd model_server && uv run uvicorn main:app --port 8100
  国内下载模型权重：HF_ENDPOINT=https://hf-mirror.com
"""
from __future__ import annotations

import os

import torch
from fastapi import FastAPI
from pydantic import BaseModel

MODEL_NAME = os.environ.get("EMBED_MODEL", "microsoft/codebert-base")
DEVICE = os.environ.get("MODEL_DEVICE", "cuda")  # 无 GPU 时设 MODEL_DEVICE=cpu
MAX_LEN = int(os.environ.get("EMBED_MAX_LEN", "512"))

app = FastAPI(title="CodeRAG Model Server", version="0.2.0")
_tokenizer = None
_model = None
_dim: int | None = None


def _resolve_device() -> str:
    """cuda 不可用时自动回退 cpu（避免无 GPU 机器加载即报错）。"""
    if DEVICE == "cuda" and not torch.cuda.is_available():
        return "cpu"
    return DEVICE


def _load():
    """惰性加载 tokenizer + model；返回 (tokenizer, model, dim)。"""
    global _tokenizer, _model, _dim
    if _model is None:
        from transformers import AutoModel, AutoTokenizer

        device = _resolve_device()
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        _model = AutoModel.from_pretrained(MODEL_NAME)
        _model.to(device)
        _model.eval()
        _dim = _model.config.hidden_size
    return _tokenizer, _model, _dim


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dim: int


@torch.inference_mode()
def _encode(texts: list[str]) -> tuple[list[list[float]], int]:
    tokenizer, model, dim = _load()
    if not texts:
        return [], dim
    enc = tokenizer(
        texts, padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt",
    ).to(_resolve_device())
    out = model(**enc)
    last = out.last_hidden_state                       # (B, L, H)
    mask = enc["attention_mask"].unsqueeze(-1).float()  # (B, L, 1)
    summed = torch.sum(last * mask, dim=1)             # (B, H)
    counts = torch.clamp(mask.sum(dim=1), min=1e-9)    # (B, 1)
    pooled = summed / counts                           # mean-pool（mask 感知）
    pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)  # L2 归一化
    return pooled.cpu().tolist(), dim


@app.get("/health")
def health():
    try:
        _, _, dim = _load()
        return {"status": "ok", "model": MODEL_NAME, "dim": dim, "device": _resolve_device()}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "model": MODEL_NAME, "error": f"{type(e).__name__}: {e}"}


@app.post("/embeddings", response_model=EmbedResponse)
def embeddings(req: EmbedRequest):
    vecs, dim = _encode(req.texts)
    return EmbedResponse(embeddings=vecs, dim=dim)
