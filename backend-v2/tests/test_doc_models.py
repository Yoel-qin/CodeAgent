from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.doc import DocSection, Document, MediaChunk


def test_doc_models_create_and_query():
    engine = create_engine("sqlite+pysqlite:///:memory:")  # 结构冒烟，不依赖 PG 方言
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        doc = Document(repo="mini", doc_name="指南.md", module="guide",
                       source_path="docs/指南.md", doc_type="markdown",
                       status="COMPLETED", file_hash="ab12", parse_meta={})
        s.add(doc)
        s.flush()
        s.add(DocSection(document_id=doc.id, repo="mini", anchor="quick/start",
                         title="快速开始", level=2, kind="text",
                         content="...", token_count=5, order_index=0))
        s.add(MediaChunk(document_id=doc.id, repo="mini", kind="image",
                         description="", page=3))
        s.commit()
        assert s.query(DocSection).filter_by(repo="mini").count() == 1
        assert s.query(MediaChunk).count() == 1
