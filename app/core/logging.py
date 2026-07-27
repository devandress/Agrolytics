"""Loguru-based logging configuration."""

import sys

from loguru import logger

from app.core.config import settings


def setup_logging() -> None:
    """Configure loguru sink with the level set in settings."""
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.LOG_LEVEL,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )
    logger.add(
        "logs/agrolytics.log",
        rotation="10 MB",
        retention="14 days",
        level=settings.LOG_LEVEL,
        enqueue=True,
    )
