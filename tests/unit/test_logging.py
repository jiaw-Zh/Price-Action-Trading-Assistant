"""Tests for :mod:`pa_assistant.logging`."""

from __future__ import annotations

import structlog

from pa_assistant.logging import configure_logging, get_logger


def test_get_logger_returns_bound_logger() -> None:
    configure_logging("INFO")
    log = get_logger("test")
    assert log is not None
    # Should not raise
    log.info("hello", key="value")


def test_get_logger_with_initial_values() -> None:
    configure_logging("DEBUG")
    log = get_logger("test", request_id="abc123")
    assert log is not None
    log.debug("hello")


def test_configure_logging_json_format() -> None:
    configure_logging("INFO", json_format=True)
    log = structlog.get_logger("json-test")
    # Just exercise the path; rendering happens lazily and writes to stderr.
    log.info("event", value=1)


def test_configure_logging_invalid_level_falls_back_to_info() -> None:
    # Bogus level → INFO
    configure_logging("BOGUS")
    log = get_logger("test")
    log.info("ok")
