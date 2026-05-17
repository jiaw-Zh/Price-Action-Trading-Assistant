"""Structured logging via :mod:`structlog`.

Usage:

    from pa_assistant.logging import configure_logging, get_logger

    configure_logging("INFO")
    log = get_logger(__name__)
    log.info("kline_received", symbol="BTCUSDT", close=67_123.45)

In production, set ``LOG_JSON=true`` to emit machine-parseable JSON lines.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor


def configure_logging(level: str = "INFO", *, json_format: bool = False) -> None:
    """Configure :mod:`structlog` and the stdlib :mod:`logging` module.

    Args:
        level: Minimum log level name (case-insensitive).
        json_format: Emit JSON-formatted lines (recommended in production).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor
    if json_format:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Tame stdlib loggers so 3rd-party libs use the same level threshold.
    logging.basicConfig(
        level=log_level,
        stream=sys.stderr,
        format="%(message)s",
        force=True,
    )


def get_logger(name: str | None = None, **initial_values: Any) -> Any:
    """Get a bound :mod:`structlog` logger.

    Args:
        name: Logger name (typically ``__name__``).
        **initial_values: Static context bound to every record from this logger.
    """
    logger = structlog.get_logger(name)
    if initial_values:
        logger = logger.bind(**initial_values)
    return logger
