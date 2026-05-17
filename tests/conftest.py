"""Shared fixtures.

The autouse fixture below clears all environment variables that ``Settings``
cares about, so tests run in a deterministic, isolated environment.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from pa_assistant.config import reset_settings

_MANAGED_PREFIXES = (
    "BINANCE_",
    "COINGLASS_",
    "TELEGRAM_",
    "APP_",
    "LOG_",
    "DUCKDB_",
    "API_",
    "OI_",
    "HTTP_",
)
_MANAGED_KEYS = {"SYMBOL", "TIMEFRAMES"}


@pytest.fixture(autouse=True)
def _isolated_settings_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    """Run each test from a clean working directory & env (no real .env leaking in)."""
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        upper = key.upper()
        if upper.startswith(_MANAGED_PREFIXES) or upper in _MANAGED_KEYS:
            monkeypatch.delenv(key, raising=False)
    reset_settings()
    yield
    reset_settings()
