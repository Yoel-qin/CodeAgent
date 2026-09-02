from pathlib import Path
from unittest.mock import MagicMock

from app.pipeline.ingest_doc import ingest_doc_file


def test_ingest_markdown_end_to_end(tmp_path, session, monkeypatch):
    monkeypatch.setattr("app.pipeline.ingest_doc.upload_original", lambda *a, **k: "v2/mini/x.md")
    monkeypatch.setattr("app.pipeline.ingest_doc.embed_texts", lambda texts, **k: [[0.1] * 1024 for _ in texts])
    upserted = []
    monkeypatch.setattr("app.pipeline.ingest_doc.upsert_sections",
                        lambda rows: upserted.extend(rows) or len(rows))
    indexed = []
    monkeypatch.setattr("app.pipeline.ingest_doc.bulk_index_sections",
                        lambda docs: indexed.extend(docs) or len(docs))

    res = ingest_doc_file(session, repo="mini",
                          file_path=Path("docs/guide.md"),
                          data="# 标题\n\n内容甲。\n".encode())
    assert res["sections"] >= 1 and res["embedded"] == res["sections"]
    assert len(upserted) == res["sections"] and len(indexed) == res["sections"]


def test_ingest_idempotent_skip(tmp_path, session, monkeypatch):
    data = b"# A\n\nb\n"
    for patch_target in ("upload_original",):
        monkeypatch.setattr(f"app.pipeline.ingest_doc.{patch_target}", lambda *a, **k: None)
    monkeypatch.setattr("app.pipeline.ingest_doc.embed_texts", lambda texts, **k: [])
    monkeypatch.setattr("app.pipeline.ingest_doc.upsert_sections", lambda rows: 0)
    monkeypatch.setattr("app.pipeline.ingest_doc.bulk_index_sections", lambda docs: 0)

    first = ingest_doc_file(session, repo="mini", file_path=Path("d/a.md"), data=data)
    second = ingest_doc_file(session, repo="mini", file_path=Path("d/a.md"), data=data)
    assert first["skipped"] is not True
    assert second.get("skipped") is True


def test_ingest_different_hash_replaces(tmp_path, session, monkeypatch):
    """I-3: 不同 hash 重跑 → PG sections 替换非累积、Milvus delete 被调。"""
    monkeypatch.setattr("app.pipeline.ingest_doc.upload_original", lambda *a, **k: None)
    monkeypatch.setattr("app.pipeline.ingest_doc.embed_texts",
                        lambda texts, **k: [[0.1] * 1024 for _ in texts])
    mock_mc = MagicMock()
    monkeypatch.setattr("app.pipeline.ingest_doc.get_client", lambda: mock_mc)
    mock_es = MagicMock()
    monkeypatch.setattr("app.pipeline.ingest_doc.get_es", lambda: mock_es)
    upserted = []
    monkeypatch.setattr("app.pipeline.ingest_doc.upsert_sections",
                        lambda rows: upserted.extend(rows) or len(rows))
    monkeypatch.setattr("app.pipeline.ingest_doc.bulk_index_sections", lambda docs: len(docs))

    # First ingest
    res1 = ingest_doc_file(session, repo="mini", file_path=Path("d/a.md"),
                           data=b"# Title1\n\nContent1\n")
    assert res1["skipped"] is not True
    n1 = res1["sections"]
    assert n1 >= 1

    # Second ingest with different content (different hash)
    res2 = ingest_doc_file(session, repo="mini", file_path=Path("d/a.md"),
                           data=b"# Title2\n\nContent2\n")
    assert res2["skipped"] is not True

    # PG: sections replaced, not accumulated
    from app.db.models.doc import DocSection, Document  # noqa: E402

    expected_name = Path("d/a.md").as_posix()[:512]  # always forward slash
    doc = session.query(Document).filter_by(repo="mini", doc_name=expected_name).first()
    assert doc is not None
    count = session.query(DocSection).filter_by(document_id=doc.id).count()
    assert count == res2["sections"]  # not n1 + n2

    # Content is from second ingest
    secs = session.query(DocSection).filter_by(document_id=doc.id).all()
    contents = " ".join(s.content for s in secs)
    assert "Content2" in contents
    assert "Content1" not in contents

    # Milvus delete called with repo filter (I-1)
    assert mock_mc.delete.call_count >= 1
    delete_call = mock_mc.delete.call_args_list[-1]
    assert 'repo == "mini"' in delete_call.kwargs.get('filter', '')

    # ES delete_by_query called with repo filter (I-1)
    assert mock_es.delete_by_query.call_count >= 1
    es_call = mock_es.delete_by_query.call_args_list[-1]
    assert es_call.kwargs.get('index') == 'v2_doc_sections'
    es_body = es_call.kwargs.get('body', {})
    assert 'repo' in str(es_body)

    # Milvus upsert called for both ingests
    assert len(upserted) == n1 + res2["sections"]
