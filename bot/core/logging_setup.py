"""Logging infrastructure.

Provides a `setup_logging()` factory that wires:
  * a rotating file handler (size-capped, N backups)
  * a stream handler for console output
  * a compact custom formatter with millisecond precision
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Final

_LOG_FORMAT: Final[str] = (
    "[{asctime}.{msecs:03.0f}] [{levelname:<8}] {name}:{lineno} - {message}"
)
_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"


class MillisecondFormatter(logging.Formatter):
    """Formatter subclass converting `msecs` into the timestamp cleanly."""

    def format(self, record: logging.LogRecord) -> str:
        record.msecs = float(record.msecs)
        return super().format(record)


def setup_logging(log_dir: Path, level: str = "INFO", max_bytes: int = 2_000_000, backup_count: int = 5) -> logging.Logger:
    """Configure the root `gacha` logger tree and return the bot logger."""
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("gacha")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    formatter = MillisecondFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT, style="{")

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "gacha-bot.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(getattr(logging, level, logging.INFO))
    console.setFormatter(formatter)
    root.addHandler(console)

    # Quiet noisy third-party loggers.
    for noisy in ("discord", "discord.http", "sqlalchemy", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root.propagate = False
    logger = logging.getLogger("gacha.bot")
    logger.info("Logging initialised (dir=%s, console_level=%s)", log_dir, level)
    return logger
