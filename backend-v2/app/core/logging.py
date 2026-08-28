"""loguru 统一配置（照搬旧库模式：控制台 + 等级受 settings.log_level）。"""
import sys

from loguru import logger

from app.core.config import settings


def setup_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level.upper())
