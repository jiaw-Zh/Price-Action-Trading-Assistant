"""Tests for the :mod:`pa_assistant.cli` Typer app."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from pa_assistant.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Meta commands
# ---------------------------------------------------------------------------


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "pa-assistant" in result.stdout


def test_init_db_creates_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "out.duckdb"
    monkeypatch.setenv("DUCKDB_PATH", str(db_path))

    result = runner.invoke(app, ["init-db"])
    assert result.exit_code == 0, result.stdout
    assert db_path.exists()
    assert "Schema initialized" in result.stdout


def test_show_config_masks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINANCE_API_KEY", "super-secret-key")
    result = runner.invoke(app, ["show-config"])
    assert result.exit_code == 0
    assert "super-secret-key" not in result.stdout


# ---------------------------------------------------------------------------
# Ingestion commands — patched httpx via MockTransport
# ---------------------------------------------------------------------------

SAMPLE_KLINES = [
    [
        1577836800000,
        "7195.24",
        "7196.25",
        "7178.66",
        "7180.00",
        "1234.5",
        1577836859999,
        "8876543.21",
        1500,
        "600.0",
        "4321098.76",
        "0",
    ],
]


@pytest.fixture
def patched_binance() -> Iterator[Callable[[Callable[[httpx.Request], httpx.Response]], None]]:
    """Patch BinanceRestClient to use a MockTransport-backed httpx client."""
    from pa_assistant.ingestion import binance as binance_module

    original_get_client = binance_module.BinanceRestClient._get_client

    container: dict[str, Callable[[httpx.Request], httpx.Response] | None] = {"handler": None}

    def patched_get_client(self: binance_module.BinanceRestClient) -> httpx.AsyncClient:
        if self._client is None:
            handler = container["handler"]
            assert handler is not None, "test forgot to set the mock handler"
            self._client = httpx.AsyncClient(
                base_url=binance_module.BINANCE_FUTURES_BASE,
                transport=httpx.MockTransport(handler),
            )
        return self._client

    def install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        container["handler"] = handler

    with patch.object(binance_module.BinanceRestClient, "_get_client", patched_get_client):
        yield install

    binance_module.BinanceRestClient._get_client = original_get_client  # type: ignore[method-assign]


def test_backfill_writes_to_duckdb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_binance: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setenv("DUCKDB_PATH", str(db_path))

    pages_returned = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal pages_returned
        pages_returned += 1
        # First call returns a single bar, second call returns empty (terminator).
        if pages_returned == 1:
            return httpx.Response(200, json=SAMPLE_KLINES)
        return httpx.Response(200, json=[])

    patched_binance(handler)

    result = runner.invoke(app, ["backfill", "--days", "1", "--symbol", "BTCUSDT"])
    assert result.exit_code == 0, result.stdout
    assert "Backfilled 1 klines" in result.stdout
    assert db_path.exists()


def test_backfill_rejects_non_1m_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The persisted timeframe is 1m only — higher TFs come from resampling.

    Accepting other intervals would silently corrupt ``kline_1m`` (the writer
    is hardcoded to that table). Validate at the CLI boundary.
    """
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setenv("DUCKDB_PATH", str(db_path))

    result = runner.invoke(app, ["backfill", "--interval", "5m"])
    assert result.exit_code != 0
    output = result.stdout + (result.stderr or "")
    assert "--interval" in output
    assert "Only '1m' is persisted" in output
    # No DB file should be created since we bail before any work.
    assert not db_path.exists()


def test_poll_oi_writes_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_binance: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setenv("DUCKDB_PATH", str(db_path))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fapi/v1/openInterest"
        return httpx.Response(
            200,
            json={
                "symbol": "BTCUSDT",
                "openInterest": "98765.4321",
                "time": 1577836800000,
            },
        )

    patched_binance(handler)

    result = runner.invoke(app, ["poll-oi", "--symbol", "BTCUSDT"])
    assert result.exit_code == 0, result.stdout
    assert "OI" in result.stdout
    assert "98,765.4321" in result.stdout

    # Verify row landed in DuckDB.
    from pa_assistant.storage import open_db

    with open_db(db_path) as db:
        rows = db.connect().execute("SELECT symbol, open_interest FROM oi_1m;").fetchall()
        assert rows == [("BTCUSDT", 98765.4321)]
