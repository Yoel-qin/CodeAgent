"""MinIO 客户端：上传原始文档（软失败）。

无 MinIO 依赖 / 连接失败 / bucket 不存在 → 返回 None，不崩。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def upload_original(repo: str, doc_name: str, data: bytes) -> str | None:
    """上传原始文档到 MinIO。成功返回 minio_key，任何异常返回 None。"""
    try:
        from minio import Minio
    except ImportError:
        return None
    try:
        endpoint = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
        access_key = os.environ.get("MINIO_ACCESS_KEY_ID", "minioadmin")
        secret_key = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
        bucket = os.environ.get("MINIO_BUCKET", "coderag")

        client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        key = f"docs/{repo}/{doc_name}"
        client.put_object(
            bucket, key, data, length=len(data), content_type="application/octet-stream",
        )
        return key
    except Exception:
        logger.debug("MinIO upload failed for %s/%s", repo, doc_name, exc_info=True)
        return None
