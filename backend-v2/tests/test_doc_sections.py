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


def test_image_elements_with_content_become_sections():
    """含描述的 IMAGE → kind="image" spec（anchor/title=图 n：描述前缀）；空 content 保持跳过。"""
    from app.pipeline.parsing.doc_element import DocElement

    els = [
        DocElement(type="PARAGRAPH", content="正文段。"),
        DocElement(type="IMAGE", content="", metadata={"image_bytes": b"x"}),   # 未描述
        DocElement(type="IMAGE", content="Eclipse 安装界面截图：选择 Install New Software，"
                                          "在 Work with 栏输入更新站点地址，勾选组件后点 Next。",
                   metadata={"image_bytes": b"y"}),
    ]
    specs = chunk_doc_elements(els, file_path="d/manual.docx", file_hash="9" * 16)
    imgs = [s for s in specs if s.kind == "image"]
    assert len(imgs) == 1                       # 空描述图不产 spec（现状保持）
    s = imgs[0]
    assert s.chunk_id.startswith("img_9999")
    assert s.level is None
    assert s.heading_path and s.heading_path[0].startswith("图 1：Eclipse 安装界面截图")
    assert "Install New Software" in s.content

    sections, _ = build_doc_rows(specs, document_id=1, repo="mini")
    img_rows = [r for r in sections if r["kind"] == "image"]
    assert len(img_rows) == 1
    assert img_rows[0]["anchor"].startswith("图-1-")   # slug 化（_SLUG_RE 保 CJK）
    assert img_rows[0]["title"].startswith("图 1：")


def test_image_seq_increments_per_described_image():
    """图片序号只数已描述图（与 ingest 的 described 计数一一对应）。"""
    from app.pipeline.parsing.doc_element import DocElement

    els = [
        DocElement(type="IMAGE", content="", metadata={}),
        DocElement(type="IMAGE", content="第一张已描述图", metadata={}),
        DocElement(type="IMAGE", content="第二张已描述图", metadata={}),
    ]
    specs = chunk_doc_elements(els, file_path="d.docx", file_hash="1" * 16)
    titles = [s.heading_path[0] for s in specs if s.kind == "image"]
    assert titles[0].startswith("图 1：") and titles[1].startswith("图 2：")
