"""清理 chunk 被引用的外键行（重新入库前调用，避免 FK 违约）。

chunk_id 变更或删除时，需先清掉 call_graph / chunk_relations 引用、
anchor_mappings、graph_embeddings、doc_resources 等依赖行。
chunk_relations.source/target 无 FK，但语义上也应随重建（build_relations 幂等重建）。
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    AnchorMapping,
    CallGraph,
    ChunkRelation,
    CodeChunk,
    DocChunk,
    DocResource,
    GraphEmbedding,
)


def clear_code_chunk_refs(session: Session, file_id: int) -> None:
    ids = select(CodeChunk.chunk_id).where(CodeChunk.file_id == file_id)
    session.execute(delete(CallGraph).where(
        CallGraph.caller_chunk_id.in_(ids) | CallGraph.callee_chunk_id.in_(ids)))
    session.execute(delete(ChunkRelation).where(
        ChunkRelation.source_chunk_id.in_(ids) | ChunkRelation.target_chunk_id.in_(ids)))
    session.execute(delete(AnchorMapping).where(AnchorMapping.code_chunk_id.in_(ids)))
    session.execute(delete(GraphEmbedding).where(GraphEmbedding.chunk_id.in_(ids)))


def clear_doc_chunk_refs(session: Session, file_id: int) -> None:
    ids = select(DocChunk.chunk_id).where(DocChunk.file_id == file_id)
    session.execute(delete(ChunkRelation).where(
        ChunkRelation.source_chunk_id.in_(ids) | ChunkRelation.target_chunk_id.in_(ids)))
    session.execute(delete(AnchorMapping).where(AnchorMapping.doc_chunk_id.in_(ids)))
    session.execute(delete(DocResource).where(DocResource.chunk_id.in_(ids)))
