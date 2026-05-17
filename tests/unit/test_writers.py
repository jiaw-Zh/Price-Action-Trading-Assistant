"""Tests for :mod:`pa_assistant.storage.writers`."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from pa_assistant.ingestion.binance import klines_to_polars
from pa_assistant.storage import (
    count_klines,
    insert_oi_snapshot,
    latest_kline_open_time,
    open_db,
    upsert_klines_1m,
)

SAMPLE_KLINES_RAW: list[list[object]] = [
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
    [
        1577836860000,
        "7180.00",
        "7185.00",
        "7175.00",
        "7182.00",
        "950.0",
        1577836919999,
        "6826543.00",
        1100,
        "500.0",
        "3593345.00",
        "0",
    ],
]


def test_upsert_klines_writes_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    df = klines_to_polars(SAMPLE_KLINES_RAW, "BTCUSDT")

    with open_db(db_path) as db:
        written = upsert_klines_1m(db, df)
        assert written == 2
        assert count_klines(db, "BTCUSDT") == 2


def test_upsert_klines_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    df = klines_to_polars(SAMPLE_KLINES_RAW, "BTCUSDT")

    with open_db(db_path) as db:
        upsert_klines_1m(db, df)
        upsert_klines_1m(db, df)  # second write should not duplicate
        assert count_klines(db, "BTCUSDT") == 2


def test_upsert_klines_replaces_on_conflict(tmp_path: Path) -> None:
    """Re-fetching the same time range must overwrite older values."""
    db_path = tmp_path / "test.duckdb"

    with open_db(db_path) as db:
        first = klines_to_polars(SAMPLE_KLINES_RAW, "BTCUSDT")
        upsert_klines_1m(db, first)

        # Same open_time, different close → update expected
        updated_raw = list(SAMPLE_KLINES_RAW)
        updated_raw[0] = list(SAMPLE_KLINES_RAW[0])
        updated_raw[0][4] = "9999.99"  # new close price
        updated = klines_to_polars(updated_raw, "BTCUSDT")
        upsert_klines_1m(db, updated)

        conn = db.connect()
        row = conn.execute(
            "SELECT close FROM kline_1m WHERE open_time = '2020-01-01 00:00:00';"
        ).fetchone()
        assert row is not None
        assert row[0] == 9999.99


def test_upsert_klines_empty_df_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    empty = klines_to_polars([], "BTCUSDT")
    with open_db(db_path) as db:
        assert upsert_klines_1m(db, empty) == 0


def test_upsert_klines_rejects_missing_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    df = pl.DataFrame({"open_time": [datetime(2020, 1, 1)], "symbol": ["BTCUSDT"]})
    with open_db(db_path) as db, pytest.raises(ValueError, match="missing columns"):
        upsert_klines_1m(db, df)


def test_latest_kline_open_time(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    with open_db(db_path) as db:
        assert latest_kline_open_time(db, "BTCUSDT") is None

        df = klines_to_polars(SAMPLE_KLINES_RAW, "BTCUSDT")
        upsert_klines_1m(db, df)

        latest = latest_kline_open_time(db, "BTCUSDT")
        assert latest == datetime(2020, 1, 1, 0, 1, 0)


def test_insert_oi_snapshot_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    ts = datetime(2020, 1, 1, 0, 0, 0)

    with open_db(db_path) as db:
        insert_oi_snapshot(db, symbol="BTCUSDT", timestamp=ts, open_interest=12345.6)
        # Second write at same (symbol, ts) replaces value
        insert_oi_snapshot(db, symbol="BTCUSDT", timestamp=ts, open_interest=99999.9)

        conn = db.connect()
        rows = conn.execute("SELECT symbol, open_interest FROM oi_1m;").fetchall()
        assert rows == [("BTCUSDT", 99999.9)]


def test_insert_oi_snapshot_with_notional(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    ts = datetime(2020, 1, 1, 0, 0, 0)
    with open_db(db_path) as db:
        insert_oi_snapshot(
            db,
            symbol="BTCUSDT",
            timestamp=ts,
            open_interest=100.0,
            notional_usd=7_180_000.0,
        )
        conn = db.connect()
        rows = conn.execute("SELECT open_interest, notional_usd FROM oi_1m;").fetchall()
        assert rows == [(100.0, 7_180_000.0)]
