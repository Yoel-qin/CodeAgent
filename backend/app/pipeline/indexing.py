"""统一索引一致性层（设计 §9.3 写入顺序：PG → Milvus → ES）。

把原本在 ``ingest_code`` / ``ingest_doc`` 各自内联的「向 ES 写全文 + 向 Milvus 写向量 +
失败置 ``embedding_synced=False``」逻辑收敛到一处，并提供 ``embedding_synced`` 补偿
（``resync_pending_embeddings``）——把因嵌入/编码器瞬时不可用而停在 ``False`` 的 chunk
重新向量化并翻回 ``True``，避免向量召回永久丢失这些 chunk。

设计要点：
- ``embed_text_for`` 是嵌入文本的**唯一真相源**（ingest 与补偿都从这里取文本，保证补偿
  重算的向量与首次入库一致）。逐字迁移自原 ``ingest_code._code_embed_text``：代码 =
  method_signature + javadoc + content；文档 = content。
- 批处理在**本层**做（``embedding_client`` / ``milvus_client`` 均无批处理）；按
  ``settings.embed_batch_size`` 切片后调编码器与 upsert。
- 嵌入未启用（无 Key / ``code_embedding_enabled=False``）时补偿为 **no-op**（不读 DB、
  不调编码器），否则循环会在 401 上空转。
- 补偿只管 **Milvus**（``embedding_synced``）；ES 无 synced 标志位，其 ``index_chunks_safe``
  已自吞错误，ES 重索引不在本期范围。
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.clients import embedding_client, es_client, milvus_client
from app.core.config import settings
from app.db.models import CodeChunk, DocChunk

_CHUNK_MODEL = {"code": CodeChunk, "doc": DocChunk}


def embed_text_for(kind: str, chunk) -> str:
    """组装送入编码器的文本。

    代码 = method_signature + javadoc + content（空段过滤后换行拼接）；
    文档 = content 原文。duck-typed：接受 ORM 行或 parser spec（都暴露这些属性）。
    语义与原 ``ingest_code._code_embed_text`` 完全一致（向量已入库，改动会使新旧失配）。
    """
    if kind == "code":
        parts = [
            getattr(chunk, "method_signature", None),
            getattr(chunk, "javadoc", None),
            getattr(chunk, "content", None),
        ]
        return "\n".join(p for p in parts if p)
    return getattr(chunk, "content", None) or ""


def _embed_enabled_for(strategy: str, kind: str) -> bool:
    """该 (strategy, kind) 组合的编码器是否就绪（镜像原 ingest_code 的就绪判断）。

    ``dual`` 下代码侧看 ``code_enabled()``（CodeBERT），其余看文档侧 ``enabled()``（BGE-M3 API）。
    仅查配置，不探测可达性——不可达由调用方的 try/except 兜底。
    """
    if kind == "code" and strategy == "dual":
        return embedding_client.code_enabled()
    return embedding_client.enabled()


def index_chunks_to_es(file_path: str, docs: list[dict]) -> None:
    """写 ES 全文索引（``es_client.index_chunks_safe`` 已 delete-then-bulk 且自吞错误）。"""
    es_client.index_chunks_safe(file_path, docs)


def index_chunks_to_milvus(strategy: str, kind: str, chunk_rows: list[dict]) -> bool:
    """把 ``chunk_rows=[{chunk_id, text}]`` 嵌入并 upsert 进 Milvus。

    **不碰 DB**。按 ``settings.embed_batch_size`` 切片，每批独立 try/except + 日志；
    永不抛异常（保证 ingest 的 PG 写入不被影响）。返回 True 当且仅当所有批次成功。
    """
    if not chunk_rows:
        return True
    batch_size = max(1, settings.embed_batch_size)
    all_ok = True
    for start in range(0, len(chunk_rows), batch_size):
        batch = chunk_rows[start:start + batch_size]
        try:
            texts = [r["text"] for r in batch]
            vecs = embedding_client.ingest_embed(kind, texts)
            milvus_client.upsert_vectors(
                strategy, kind,
                [{"chunk_id": r["chunk_id"], "embedding": vecs[i]} for i, r in enumerate(batch)],
            )
        except Exception as e:  # 单批失败不阻断其它批
            logger.warning(
                f"[indexing] Milvus 批次失败 kind={kind} start={start} size={len(batch)} "
                f"{type(e).__name__}: {e}"
            )
            all_ok = False
    return all_ok


def delete_chunks_from_milvus(strategy: str, kind: str, chunk_ids: list[str]) -> bool:
    """从 Milvus 按 chunk_id 硬删除向量（对称于 ``index_chunks_to_milvus``）。

    **不碰 DB**。自吞异常（Milvus 不可用不得阻断 PG 写入/同步），失败仅记日志——
    孤儿向量可容忍，后续可由对账任务清理。空列表为 no-op，返回 True。
    """
    if not chunk_ids:
        return True
    try:
        milvus_client.delete_vectors(strategy, kind, chunk_ids)
        return True
    except Exception as e:
        logger.warning(
            f"[indexing] Milvus 删除失败 kind={kind} n={len(chunk_ids)} "
            f"{type(e).__name__}: {e}"
        )
        return False


def _load_unsynced_chunks(session: Session, kind: str, limit: int | None) -> list[dict]:
    """加载 ``embedding_synced=False AND is_deleted=False`` 的 chunk（**本模块唯一 ORM 触点**）。

    返回 ``[{chunk_id, text}]``（text 已由 ``embed_text_for`` 组装）。抽成独立函数便于测试 monkeypatch。
    """
    model = _CHUNK_MODEL[kind]
    stmt = select(model).where(
        model.embedding_synced == False,  # noqa: E712
        model.is_deleted == False,  # noqa: E712
    )
    if limit:
        stmt = stmt.limit(limit)
    rows = session.execute(stmt).scalars().all()
    return [{"chunk_id": r.chunk_id, "text": embed_text_for(kind, r)} for r in rows]


def _mark_synced(session: Session, kind: str, chunk_ids: list[str]) -> None:
    """批量置 ``embedding_synced=True``（一条 UPDATE，镜像原 ingest_code 的批量写）。"""
    if not chunk_ids:
        return
    model = _CHUNK_MODEL[kind]
    session.execute(
        update(model).where(model.chunk_id.in_(chunk_ids)).values(embedding_synced=True)
    )


def resync_pending_embeddings(
    session: Session, *, strategy: str | None = None, limit: int | None = None,
    commit_each_batch: bool = True,
) -> dict:
    """补偿：重新向量化所有 ``embedding_synced=False`` 的 chunk 并翻回 True。

    对 ``code`` / ``doc`` 分别处理：编码器未启用则记 ``skipped=True`` 并跳过（no-op）；
    否则按批 embed + upsert，成功批 ``_mark_synced``（可选每批 commit，长扫描持久化部分进度），
    失败批计数后继续（留 False）。返回 ``{"code": {total,synced,failed,skipped}, "doc": {...}}``。
    """
    if strategy is None:
        strategy = settings.embedding_strategy
    result: dict[str, dict] = {}
    for kind in ("code", "doc"):
        if not _embed_enabled_for(strategy, kind):
            result[kind] = {"total": 0, "synced": 0, "failed": 0, "skipped": True}
            logger.info(f"[indexing] 补偿跳过 kind={kind}（编码器未启用）")
            continue

        rows = _load_unsynced_chunks(session, kind, limit)
        total = len(rows)
        batch_size = max(1, settings.embed_batch_size)
        synced = failed = 0
        for start in range(0, total, batch_size):
            batch = rows[start:start + batch_size]
            if index_chunks_to_milvus(strategy, kind, batch):
                _mark_synced(session, kind, [r["chunk_id"] for r in batch])
                synced += len(batch)
                if commit_each_batch:
                    session.commit()
            else:
                failed += len(batch)
        result[kind] = {"total": total, "synced": synced, "failed": failed, "skipped": False}
        logger.info(
            f"[indexing] 补偿完成 kind={kind} total={total} synced={synced} failed={failed}"
        )
    return result
