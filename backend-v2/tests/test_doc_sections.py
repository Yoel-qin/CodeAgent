from app.pipeline.chunking.doc_chunker import chunk_doc_elements
from app.pipeline.chunking.doc_sections import build_doc_rows
from app.pipeline.parsing.doc_element import DocElement


def _elements():
    return [
        DocElement(type="HEADING", content="快速开始", heading_level=1, heading_path=[]),
        DocElement(type="PARAGRAPH", content="第一步安装。", heading_path=["快速开始"], heading_level=1),
        DocElement(type="HEADING", content="配置", heading_level=2, heading_path=["快速开始"]),
        DocElement(type="PARAGRAPH", content="配置项甲乙丙。", heading_path=["快速开始", "配置"], heading_level=2),
    ]


def test_chunk_then_rows_roundtrip():
    specs = chunk_doc_elements(_elements(), file_path="docs/guide.md", file_hash="abcdef123456")
    sections, media = build_doc_rows(specs, document_id=7, repo="mini")
    assert media == []
    assert len(sections) >= 2
    cfg = next(s for s in sections if "配置" in s["title"])
    assert cfg["anchor"].startswith("快速开始/配置") or "配置" in cfg["anchor"]
    assert cfg["level"] == 2
    assert cfg["document_id"] == 7 and cfg["repo"] == "mini"
    assert all(s["order_index"] >= 0 for s in sections)


def test_anchor_collision_disambiguated():
    els = []
    for _ in range(2):
        els.append(DocElement(type="HEADING", content="同名", heading_level=1, heading_path=[]))
        els.append(DocElement(type="PARAGRAPH", content="内容。", heading_path=["同名"], heading_level=1))
    specs = chunk_doc_elements(els, file_path="d.md", file_hash="0" * 16)
    sections, _ = build_doc_rows(specs, document_id=1, repo="mini")
    anchors = [s["anchor"] for s in sections]
    assert len(anchors) == len(set(anchors)), "同文档 anchor 必须唯一"
