"""CodeRAG 本地模型服务：BGE-M3 文本嵌入（GPU 优先，CPU 回退）。

端点：
  GET  /health
  POST /embeddings  {"texts": [...]} -> {"embeddings": [[...]], "dim": 1024}
Phase 2+ 增 /rerank；Phase 5 增图嵌入。
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import BaseModel

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
DEVICE = os.environ.get("MODEL_DEVICE", "cuda")  # 无 GPU 时容器外设 DEVICE=cpu

app = FastAPI(title="CodeRAG Model Server", version="0.1.0")
_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME, device=DEVICE)
    return _model


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dim: int


@app.get("/health")
def health():
    try:
        m = get_model()
        return {
            "status": "ok",
            "model": MODEL_NAME,
            "dim": m.get_sentence_embedding_dimension(),
            "device": str(m.device),
        }
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "model": MODEL_NAME, "error": f"{type(e).__name__}: {e}"}


@app.post("/embeddings", response_model=EmbedResponse)
def embeddings(req: EmbedRequest):
    m = get_model()
    vecs = m.encode(req.texts, normalize_embeddings=True, convert_to_numpy=True)
    return EmbedResponse(embeddings=vecs.tolist(), dim=int(vecs.shape[1]))
