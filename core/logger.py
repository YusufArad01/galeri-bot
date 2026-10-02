"""Loguru configuration: colored console output plus rotating file log."""

from __future__ import annotations

import sys

from loguru import logger

from core.config import settings

_CONSOLE_FORMAT: str = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

_FILE_FORMAT: str = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS ZZ} | {level: <8} | "
    "pid={process} thread={thread.name} | "
    "{name}:{function}:{line} - {message} | extra={extra}"
)

_configured: bool = False


def setup_logging() -> None:
    """Configure sinks once per process. Safe to call multiple times."""
    global _configured
    if _configured:
        return

    logger.remove()

    logger.add(
        sys.stdout,
        format=_CONSOLE_FORMAT,
        level=settings.LOG_LEVEL,
        colorize=True,
        backtrace=False,
        diagnose=False,
        enqueue=True,
    )

    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(
        settings.LOG_DIR / "app.log",
        format=_FILE_FORMAT,
        level="DEBUG",
        rotation="50 MB",
        retention="14 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        # diagnose=False keeps local variable values (proxy credentials, tokens) out of tracebacks.
        diagnose=False,
    )

    _configured = True
    logger.debug("Logging configured (console level={}, file={})", settings.LOG_LEVEL, settings.LOG_DIR / "app.log")


__all__ = ["logger", "setup_logging"]
