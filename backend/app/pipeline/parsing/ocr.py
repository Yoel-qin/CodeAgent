"""OCR（Phase 1.5b）：图片文字提取，用 ``rapidocr-onnxruntime``（PaddleOCR 模型的 ONNX 移植）。

懒加载单例；**任一环节失败（import / 模型下载 / 运行）都优雅降级为空串**——图片仍会提取入库，
仅 OCR 文本为空（按上下文索引），沿用项目「无 key/服务不可用即降级」范式（CLAUDE.md）。
首用会拉模型；CN 网络失败时降级路径保证图片管道不中断。
"""
from __future__ import annotations

from loguru import logger

_engine = None
_unavailable = False


def _get_engine():
    global _engine, _unavailable
    if _unavailable:
        return None
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
            logger.info("[ocr] RapidOCR 就绪")
        except Exception as e:
            logger.warning(f"[ocr] RapidOCR 不可用，图片 OCR 降级（文本为空）: {type(e).__name__}: {e}")
            _unavailable = True
            return None
    return _engine


def ocr_image(image_bytes: bytes) -> str:
    """OCR 图片字节 → 文本；不可用/无文字/失败返回 ''。"""
    engine = _get_engine()
    if engine is None or not image_bytes:
        return ""
    try:
        result, _elapse = engine(image_bytes)        # bytes / path / ndarray 均可
        if not result:
            return ""
        # result: list of [box, text, score]
        return " ".join(line[1] for line in result if line and len(line) > 1 and line[1]).strip()
    except Exception as e:
        logger.warning(f"[ocr] 识别失败: {type(e).__name__}: {e}")
        return ""


def reset_for_test() -> None:
    """测试用：重置单例与降级标记。"""
    global _engine, _unavailable
    _engine = None
    _unavailable = False
