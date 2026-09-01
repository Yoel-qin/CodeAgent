from pathlib import Path

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
