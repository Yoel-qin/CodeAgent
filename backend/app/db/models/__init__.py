"""所有 ORM 模型聚合（导入即注册到 Base.metadata，供 Alembic autogenerate）。"""
from __future__ import annotations

from app.db.base import Base
from app.db.models.chat import ChatMessage, Conversation
from app.db.models.code import CallGraph, CodeChunk, CodeFile
from app.db.models.doc import DocChunk, DocFile, DocResource
from app.db.models.eval import CandidateEvalQuery, EvalRun
from app.db.models.graph import GraphEmbedding
from app.db.models.history import ChangeHistory, DocUpdateProposal, RollbackHistory, SyncTask
from app.db.models.relation import AnchorMapping, ChunkRelation
from app.db.models.system import RankingModelConfig, RetrievalLog

__all__ = [
    "Base",
    "CodeFile",
    "CodeChunk",
    "CallGraph",
    "DocFile",
    "DocChunk",
    "DocResource",
    "EvalRun",
    "CandidateEvalQuery",
    "ChunkRelation",
    "AnchorMapping",
    "ChangeHistory",
    "SyncTask",
    "RollbackHistory",
    "DocUpdateProposal",
    "GraphEmbedding",
    "RetrievalLog",
    "RankingModelConfig",
    "Conversation",
    "ChatMessage",
]
