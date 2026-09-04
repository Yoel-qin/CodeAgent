"""文档读侧服务（M6 Task 1）。

约定沿 chat_service：session 由调用方注入、只读不 commit；读侧统一 ``select()``
全量加载（ORM 实体整行取回），避免 async 会话对过期属性的同步访问触发
MissingGreenlet。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.doc import DocSection, Document


async def list_documents(
    session: AsyncSession,
    *,
    repo: str | None,
    limit: int,
    offset: int,
    repos: list[str] | None = None,
) -> tuple[int, list[tuple[Document, int]]]:
    """文档列表（id 倒序 + limit/offset），伴随每篇的 section 数。

    section 数用 ``outerjoin + count(DocSection.id) + group_by`` 一条 SQL 取回——
    无节的文档计 0（``count(*)`` 会错计成 1）。``repo`` 为空 = 不过滤。
    ``repos``（RBAC 可见仓库列表，None = 不过滤；M9）——count 与页查询同步过滤。
    total 与页数据分开取：聚合查询带 group_by，语义是「分组数」而非全量行数，
    单独一条 ``count(*)`` 才是过滤后的 total。
    """
    count_stmt = select(func.count()).select_from(Document)
    if repo:
        count_stmt = count_stmt.where(Document.repo == repo)
    if repos is not None:
        count_stmt = count_stmt.where(Document.repo.in_(repos))
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = select(Document, func.count(DocSection.id))
    if repo:
        stmt = stmt.where(Document.repo == repo)
    if repos is not None:
        stmt = stmt.where(Document.repo.in_(repos))
    stmt = (
        stmt.outerjoin(DocSection, DocSection.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()
    return total, [(row[0], row[1]) for row in rows]


async def get_document_with_sections(session: AsyncSession, document_id: int) -> dict | None:
    """文档详情 + 全部节（order_index 升序，同刻以 id 升序 tie-break）；无此文档 → None。

    节内容整段返回（前端阅读视图要渲染正文），不做任何截断。
    """
    row = await session.execute(select(Document).where(Document.id == document_id))
    doc = row.scalars().first()
    if doc is None:
        return None
    rows = await session.execute(
        select(DocSection)
        .where(DocSection.document_id == document_id)
        .order_by(DocSection.order_index, DocSection.id)
    )
    sections = rows.scalars().all()
    return {
        "document": {
            "id": doc.id,
            "repo": doc.repo,
            "doc_name": doc.doc_name,
            "module": doc.module,
            "doc_type": doc.doc_type,
            "status": doc.status,
            "created_at": doc.created_at,
        },
        "sections": [
            {
                "id": s.id,
                "anchor": s.anchor,
                "title": s.title,
                "level": s.level,
                "kind": s.kind,
                "token_count": s.token_count,
                "page": s.page,
                "content": s.content,
            }
            for s in sections
        ],
    }
