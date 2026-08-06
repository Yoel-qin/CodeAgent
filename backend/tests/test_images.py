"""Phase 1.5b 图片管道单测（无基础设施，OCR/MinIO mock）：
过滤（<50px/纯色）/ aHash 去重 / 缩略图 / OCR 降级 / process_images 组 spec+resource。"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.pipeline import images
from app.pipeline.parsing import ocr
from app.pipeline.parsing.doc_element import DocElement


def _png(size=(120, 120)) -> bytes:
    """非纯色 PNG（白底 + 黑块），通过空白过滤。"""
    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).rectangle([20, 20, 100, 100], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_is_invalid_small_and_blank():
    assert images._is_invalid(Image.new("RGB", (10, 10), (0, 0, 0)))           # <50px
    assert images._is_invalid(Image.new("RGB", (120, 120), (255, 255, 255)))  # 纯色空白
    assert not images._is_invalid(Image.open(io.BytesIO(_png())))              # 有内容


def test_thumbnail_bytes_is_png():
    out = images._thumbnail_bytes(Image.open(io.BytesIO(_png((400, 400)))))
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = Image.open(io.BytesIO(out)).size
    assert max(w, h) <= images._THUMB_SIZE


def test_process_images_filters_dedup_ocr_context(monkeypatch):
    monkeypatch.setattr(images.minio_client, "put_bytes",
                        lambda key, data, content_type="x": key)
    monkeypatch.setattr(images.ocr, "ocr_image", lambda b: "订单状态 ORDER-123")

    good = _png()
    elements = [
        DocElement(type="PARAGRAPH", content="图片前的说明文字"),
        DocElement(type="IMAGE", content="", metadata={"image_bytes": good, "ext": "png"}),
        DocElement(type="IMAGE", content="", metadata={"image_bytes": _png((10, 10))}),   # 过小
        DocElement(type="IMAGE", content="", metadata={"image_bytes": good}),              # 重复
        DocElement(type="IMAGE", content="", metadata={"image_bytes": _blank_png()}),      # 空白
        DocElement(type="PARAGRAPH", content="图片后的说明文字"),
    ]
    specs, resources = images.process_images(
        elements, file_path="t.pdf", file_hash="abcdef0123456789", commit_hash="C")

    assert len(specs) == 1                       # 只保留 1 张有效图（过小/重复/空白均滤除）
    s = specs[0]
    assert s.chunk_content_type == "image"
    assert s.chunk_id.startswith("img_")
    assert s.image_description == "订单状态 ORDER-123"
    assert "图片前的说明文字" in s.content and "图片后的说明文字" in s.content
    assert s.image_url and s.image_url.startswith("images/")
    assert s.image_thumbnail_url and s.image_thumbnail_url.endswith("_thumb.png")
    assert s.image_width == 120 and s.image_height == 120
    assert len(resources) == 1
    assert resources[0]["storage_path"] == s.image_url
    assert resources[0]["resource_type"] == "image"


def test_process_images_no_image_elements():
    elements = [DocElement(type="PARAGRAPH", content="只有文本")]
    specs, resources = images.process_images(
        elements, file_path="t.md", file_hash="h" * 16, commit_hash="C")
    assert specs == [] and resources == []


def test_ocr_degrade_when_unavailable(monkeypatch):
    ocr.reset_for_test()
    monkeypatch.setattr(ocr, "_get_engine", lambda: None)   # 模拟 RapidOCR 不可用
    assert ocr.ocr_image(b"some-bytes") == ""


def _blank_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (120, 120), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()
