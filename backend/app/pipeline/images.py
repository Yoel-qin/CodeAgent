"""图片处理管道（Phase 1.5b）。

对解析层产出的 IMAGE ``DocElement`` 做：**过滤**（<50px / 纯色空白）→ **去重**（aHash 同文档内）
→ **MinIO 存原图+缩略图**（Pillow）→ **OCR**（:mod:`ocr`，可插拔+降级）→ 组 **image DocChunkSpec**
+ **doc_resource** dict。

无 DB 依赖——返回 ``(specs, resource_dicts)``，由 :mod:`ingest_doc` 落库（DocResource + DocChunk）并并入索引。
图片字节由 parser 放入 ``DocElement.metadata['image_bytes']``（含 ext/width/height/bbox/page）。
"""
from __future__ import annotations

import io
import uuid

from loguru import logger
from PIL import Image

from app.clients import minio_client
from app.pipeline.metadata import approx_token_count, content_hash, extract_doc_keywords
from app.pipeline.parsing import ocr
from app.pipeline.parsing.doc_element import DocChunkSpec

_MIN_SIZE = 50            # 小于此尺寸视为无效
_THUMB_SIZE = 240         # 缩略图最长边
_CONTEXT_CHARS = 200      # 图片前后上下文字数
_TEXT_TYPES = ("PARAGRAPH", "HEADING", "LIST")

_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
         "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp"}


def _mime(ext: str) -> str:
    e = (ext or "").lower().lstrip(".")
    return _MIME.get(e, f"image/{e or 'png'}")


def _open(img_bytes: bytes) -> Image.Image | None:
    try:
        return Image.open(io.BytesIO(img_bytes))
    except Exception as e:
        logger.warning(f"[images] 打开图片失败: {type(e).__name__}: {e}")
        return None


def _is_invalid(img: Image.Image) -> bool:
    w, h = img.size
    if w < _MIN_SIZE or h < _MIN_SIZE:
        return True
    try:
        lo, hi = img.convert("L").getextrema()  # 纯色（纯白/纯黑）→ lo==hi
        if lo == hi:
            return True
    except Exception:
        pass
    return False


def _ahash(img: Image.Image) -> str:
    """8×8 平均哈希，同文档去重用。"""
    g = img.convert("L").resize((8, 8))
    pixels = list(g.tobytes())           # tobytes 避免 Pillow14 getdata 弃用
    avg = sum(pixels) / len(pixels)
    bits = 0
    for p in pixels:
        bits = (bits << 1) | (1 if p >= avg else 0)
    return f"{bits:016x}"


def _thumbnail_bytes(img: Image.Image) -> bytes:
    t = img.copy()
    t.thumbnail((_THUMB_SIZE, _THUMB_SIZE))
    buf = io.BytesIO()
    t.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _context(elements: list, idx: int) -> tuple[str, str]:
    """图片前后最近的文本元素（各取前 N 字）作上下文。"""
    before = after = ""
    for j in range(idx - 1, -1, -1):
        e = elements[j]
        if e.type in _TEXT_TYPES and e.content:
            before = e.content[:_CONTEXT_CHARS]
            break
    for j in range(idx + 1, len(elements)):
        e = elements[j]
        if e.type in _TEXT_TYPES and e.content:
            after = e.content[:_CONTEXT_CHARS]
            break
    return before, after


def process_images(elements, *, file_path: str, file_hash: str,
                   commit_hash: str = "UNKNOWN") -> tuple[list[DocChunkSpec], list[dict]]:
    """处理所有 IMAGE 元素 → (image specs, doc_resource dicts)。"""
    specs: list[DocChunkSpec] = []
    resources: list[dict] = []
    seen: set[str] = set()
    fh8 = (file_hash or "x" * 8)[:8]
    order = 0

    for idx, el in enumerate(elements):
        if el.type != "IMAGE":
            continue
        meta = el.metadata or {}
        raw = meta.get("image_bytes")
        if not raw:
            continue
        img = _open(raw)
        if img is None or _is_invalid(img):
            continue
        h = _ahash(img)
        if h in seen:
            continue
        seen.add(h)

        ext = (meta.get("ext") or "png").lower().lstrip(".")
        uid = uuid.uuid4().hex
        key = f"images/{uid}.{ext}"
        thumb_key = f"images/{uid}_thumb.png"
        try:
            minio_client.put_bytes(key, raw, content_type=_mime(ext))
            minio_client.put_bytes(thumb_key, _thumbnail_bytes(img), content_type="image/png")
        except Exception as e:
            logger.warning(f"[images] MinIO 上传失败: {type(e).__name__}: {e}")
            continue

        text = ocr.ocr_image(raw)
        before, after = _context(elements, idx)
        content = "\n".join(p for p in (before, text or "[图片]", after) if p)
        cid = f"img_{fh8}_{order}"
        w, height = img.size
        specs.append(DocChunkSpec(
            chunk_id=cid, file_path=file_path, heading_path=[], heading_level=None,
            section_order=order, content=content, content_hash=content_hash(content),
            token_count=approx_token_count(content), code_anchors=[],
            keywords=extract_doc_keywords("", content), git_commit_hash=commit_hash,
            page_number=el.page_number, chunk_content_type="image",
            image_url=key, image_thumbnail_url=thumb_key, image_description=text or None,
            image_width=w, image_height=height, context_before=before or None, context_after=after or None,
        ))
        resources.append({
            "resource_type": "image", "storage_path": key, "thumbnail_path": thumb_key,
            "file_size_bytes": len(raw), "mime_type": _mime(ext), "width": w, "height": height,
            "page_number": el.page_number, "chunk_id": cid, "description": text or None,
            "content_hash": h,
        })
        order += 1
    return specs, resources
