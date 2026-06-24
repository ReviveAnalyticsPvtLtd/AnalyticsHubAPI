"""
logging.py

Structured logging configuration for the sandbox service.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["logger"]

import sys
from loguru import logger as _logger

_logger.remove()

_logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",
    serialize=False,
)

_logger.add(
    sys.stdout,
    format="{message}",
    level="INFO",
    serialize=True,
    filter=lambda record: record["extra"].get("structured", False),
)

logger = _logger
