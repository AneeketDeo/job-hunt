import sys
from pathlib import Path
from loguru import logger
from config.settings import settings

LOG_FILE = settings.LOGS_DIR / "orchestrator.log"

logger.remove()

# Pretty console sink
logger.add(
    sys.stdout,
    level="INFO",
    colorize=True,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    ),
)

# Persistent rotated log sink
logger.add(
    str(LOG_FILE),
    level="DEBUG",
    rotation="10 MB",
    retention="14 days",
    compression="zip",
    encoding="utf-8",
    enqueue=True,
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
)

__all__ = ["logger"]
