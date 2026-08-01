"""Structured logging setup."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


class _ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        message = super().format(record)
        if sys.stderr.isatty():
            return f"{color}{message}{self.RESET}"
        return message


_LOG_BUFFER: list[str] = []
_MAX_BUFFER = 500


class RingBufferHandler(logging.Handler):
    """Keep recent log lines for the web dashboard."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            _LOG_BUFFER.append(msg)
            if len(_LOG_BUFFER) > _MAX_BUFFER:
                del _LOG_BUFFER[: len(_LOG_BUFFER) - _MAX_BUFFER]
        except Exception:  # noqa: BLE001
            self.handleError(record)


def get_recent_logs(limit: int = 100) -> list[str]:
    return _LOG_BUFFER[-limit:]


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger("photo_importer")
    if root.handlers:
        return root

    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_ColorFormatter(fmt._fmt, datefmt=fmt.datefmt))
    root.addHandler(console)

    ring = RingBufferHandler()
    ring.setFormatter(fmt)
    root.addHandler(ring)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "importer.log")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    # Quiet noisy libs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return root


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"photo_importer.{name}")
    return logging.getLogger("photo_importer")
