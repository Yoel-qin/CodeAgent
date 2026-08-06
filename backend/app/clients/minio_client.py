"""MinIO 对象存储客户端（Phase 1.5d）：存储上传的文档原件（及后续 1.5b 图片 / 表格中间产物）。

懒加载单例；首次连接确保 bucket 存在。封装 put/get/stat/remove 字节对象。MinIO 不可达时
抛异常（上传是显式操作），由上层 try/except 处理；get/remove 自吞仅告警。
"""
from __future__ import annotations

import io

from loguru import logger
from minio import Minio

from app.core.config import settings

_client: Minio | None = None


def get_client() -> Minio:
    """懒加载 MinIO 客户端，首次确保 bucket 存在。"""
    global _client
    if _client is None:
        c = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        if not c.bucket_exists(settings.minio_bucket):
            c.make_bucket(settings.minio_bucket)
            logger.info(f"[minio] created bucket {settings.minio_bucket}")
        _client = c
    return _client


def put_bytes(key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
    """上传字节数据，返回 key。"""
    get_client().put_object(
        settings.minio_bucket, key, io.BytesIO(data), length=len(data), content_type=content_type,
    )
    return key


def get_bytes(key: str) -> bytes | None:
    """下载对象为字节；不存在/失败返回 None。"""
    try:
        resp = get_client().get_object(settings.minio_bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
    except Exception as e:
        logger.warning(f"[minio] get_object {key} 失败: {type(e).__name__}: {e}")
        return None


def remove_object(key: str) -> None:
    """删除对象（best-effort，自吞）。"""
    try:
        get_client().remove_object(settings.minio_bucket, key)
    except Exception as e:
        logger.warning(f"[minio] remove_object {key} 失败: {type(e).__name__}: {e}")
