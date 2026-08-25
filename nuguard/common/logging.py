"""Structured logger: JSON format in CI, human-readable with rich locally."""

import logging
import os
import sys


def _is_ci() -> bool:
    """Return True when running inside a CI environment."""
    return os.environ.get("CI", "").lower() in {"1", "true", "yes"}


def _log_level() -> int:
    """Resolve the effective log level from NUGUARD_LOG_LEVEL (default INFO)."""
    name = os.environ.get("NUGUARD_LOG_LEVEL", "INFO").upper()
    return logging.getLevelName(name) if name in logging._nameToLevel else logging.INFO


class _JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON for machine consumption."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "ts": self.formatTime(record, self.datefmt),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


class _RichHandler(logging.StreamHandler):
    """Human-readable colourised handler that uses rich when available."""

    def __init__(self) -> None:
        try:
            from rich.console import Console
            from rich.logging import RichHandler as _RH

            self._rich_handler: logging.Handler = _RH(
                console=Console(stderr=True),
                show_time=True,
                show_path=False,
            )
        except ImportError:
            self._rich_handler = None  # type: ignore[assignment]
        super().__init__(stream=sys.stderr)

    def emit(self, record: logging.LogRecord) -> None:
        if self._rich_handler is not None:
            self._rich_handler.emit(record)
        else:
            super().emit(record)


_configured: set[str] = set()


def get_logger(name: str) -> logging.Logger:
    """Return a configured Logger for *name*.

    In CI environments (``CI=1``) records are emitted as JSON to stderr.
    Locally, records are formatted with rich when available, otherwise plain
    text. Level defaults to INFO; set ``NUGUARD_LOG_LEVEL=DEBUG`` for verbose
    diagnostics.
    """
    logger = logging.getLogger(name)

    if name in _configured:
        return logger

    level = _log_level()
    logger.setLevel(level)

    if _is_ci():
        handler: logging.Handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JsonFormatter())
    else:
        handler = _RichHandler()

    handler.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False
    _configured.add(name)
    return logger
