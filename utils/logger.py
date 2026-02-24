"""
logger.py

This module configures the Loguru logger for the application.
It sets up:
- Colored console logging for better readability during development.
- File logging with log rotation for persistent storage and debugging.

The logger writes:
- To stdout with color formatting for human-friendly logs.
- To 'logs/runLogs.log' with DEBUG level and 1 MB file rotation.

Usage:
    from logger import logger
    logger.info("Something happened")
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["logger"]


try:
    from logtail import LogtailHandler
    _HAS_LOGTAIL = True
except (ImportError, AttributeError):
    _HAS_LOGTAIL = False
from loguru import logger
import sys
import os

os.makedirs("logs", exist_ok=True)

logger.remove()

if _HAS_LOGTAIL and os.environ.get("LOGTAIL_TOKEN") and os.environ.get("LOGTAIL_HOST"):
    logtailHandler = LogtailHandler(
        source_token = os.environ.get("LOGTAIL_TOKEN"),
        host = os.environ.get("LOGTAIL_HOST")
    )
    logger.add(
        logtailHandler,
        level="INFO"
    )
logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time}</green> | <level>{level}</level> | <cyan>{message}</cyan>",
    level="INFO",
)
logger.add(
    "logs/runLogs.log",
    level="DEBUG",
    rotation="1 MB",
)