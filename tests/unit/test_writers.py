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


def test_insert_funding_weighted_basic(tmp_path: Path) -> None:
    from pa_assistant.storage import insert_funding_weighted

    db_path = tmp_path / "test.duckdb"
    ts = datetime(2020, 1, 1, 0, 0, 0)
    with open_db(db_path) as db:
        insert_funding_weighted(
            db,
            symbol="BTCUSDT",
            timestamp=ts,
            weighted_rate=-0.00012,
            source="self_aggregated",
            sample_count=3,
            raw={"binance": {"rate": 0.0001}, "okx": {"rate": -0.0002}},
        )
        conn = db.connect()
        rows = conn.execute(
            "SELECT symbol, weighted_rate, source, sample_count FROM funding_weighted;"
        ).fetchall()
        assert rows == [("BTCUSDT", -0.00012, "self_aggregated", 3)]


def test_insert_funding_weighted_pk_includes_source(tmp_path: Path) -> None:
    """Same (symbol, timestamp) but different sources must coexist."""
    from pa_assistant.storage import insert_funding_weighted

    db_path = tmp_path / "test.duckdb"
    ts = datetime(2020, 1, 1, 0, 0, 0)
    with open_db(db_path) as db:
        insert_funding_weighted(
            db,
            symbol="BTCUSDT",
            timestamp=ts,
            weighted_rate=0.0001,
            source="self_aggregated",
            sample_count=3,
            raw=None,
        )
        insert_funding_weighted(
            db,
            symbol="BTCUSDT",
            timestamp=ts,
            weighted_rate=0.00011,
            source="coinglass",
            sample_count=None,
            raw=None,
        )
        conn = db.connect()
        rows = conn.execute(
            "SELECT source, weighted_rate FROM funding_weighted ORDER BY source;"
        ).fetchall()
        assert rows == [("coinglass", 0.00011), ("self_aggregated", 0.0001)]


def test_insert_funding_weighted_idempotent_per_source(tmp_path: Path) -> None:
    from pa_assistant.storage import insert_funding_weighted

    db_path = tmp_path / "test.duckdb"
    ts = datetime(2020, 1, 1, 0, 0, 0)
    with open_db(db_path) as db:
        insert_funding_weighted(
            db,
            symbol="BTCUSDT",
            timestamp=ts,
            weighted_rate=0.0001,
            source="self_aggregated",
            sample_count=3,
            raw=None,
        )
        # Re-insert at same PK with a different value — should overwrite.
        insert_funding_weighted(
            db,
            symbol="BTCUSDT",
            timestamp=ts,
            weighted_rate=0.0009,
            source="self_aggregated",
            sample_count=2,
            raw=None,
        )
        conn = db.connect()
        rows = conn.execute("SELECT weighted_rate, sample_count FROM funding_weighted;").fetchall()
        assert rows == [(0.0009, 2)]


# ---------------------------------------------------------------------------
# OI history batch writer
# ---------------------------------------------------------------------------


SAMPLE_OI_HIST_RAW: list[dict[str, object]] = [
    {
        "symbol": "BTCUSDT",
        "sumOpenInterest": "20403.637",
        "sumOpenInterestValue": "150570784.07",
        "timestamp": 1577836800000,  # 2020-01-01 00:00:00
    },
    {
        "symbol": "BTCUSDT",
        "sumOpenInterest": "20410.000",
        "sumOpenInterestValue": "150620000.00",
        "timestamp": 1577837100000,  # 2020-01-01 00:05:00
    },
]


def test_oi_hist_to_polars_basic() -> None:
    from pa_assistant.ingestion.binance import oi_hist_to_polars

    df = oi_hist_to_polars(SAMPLE_OI_HIST_RAW, "BTCUSDT")
    assert df.height == 2
    assert df["symbol"].to_list() == ["BTCUSDT", "BTCUSDT"]
    assert df["open_interest"].to_list() == [20403.637, 20410.0]
    assert df["notional_usd"].to_list() == [150570784.07, 150620000.0]
    assert df["timestamp"].to_list() == [
        datetime(2020, 1, 1, 0, 0, 0),
        datetime(2020, 1, 1, 0, 5, 0),
    ]


def test_oi_hist_to_polars_empty() -> None:
    from pa_assistant.ingestion.binance import oi_hist_to_polars

    df = oi_hist_to_polars([], "BTCUSDT")
    assert df.is_empty()
    assert set(df.columns) == {"timestamp", "symbol", "open_interest", "notional_usd"}


def test_upsert_oi_history_writes_rows(tmp_path: Path) -> None:
    from pa_assistant.ingestion.binance import oi_hist_to_polars
    from pa_assistant.storage import upsert_oi_history

    db_path = tmp_path / "test.duckdb"
    df = oi_hist_to_polars(SAMPLE_OI_HIST_RAW, "BTCUSDT")
    with open_db(db_path) as db:
        n = upsert_oi_history(db, df)
        assert n == 2
        conn = db.connect()
        count = conn.execute("SELECT COUNT(*) FROM oi_1m").fetchone()
        assert count is not None
        assert count[0] == 2


def test_upsert_oi_history_idempotent(tmp_path: Path) -> None:
    from pa_assistant.ingestion.binance import oi_hist_to_polars
    from pa_assistant.storage import upsert_oi_history

    db_path = tmp_path / "test.duckdb"
    df = oi_hist_to_polars(SAMPLE_OI_HIST_RAW, "BTCUSDT")
    with open_db(db_path) as db:
        upsert_oi_history(db, df)
        upsert_oi_history(db, df)
        conn = db.connect()
        count = conn.execute("SELECT COUNT(*) FROM oi_1m").fetchone()
        assert count is not None
        assert count[0] == 2  # not 4


def test_upsert_oi_history_empty_is_noop(tmp_path: Path) -> None:
    from pa_assistant.ingestion.binance import oi_hist_to_polars
    from pa_assistant.storage import upsert_oi_history

    db_path = tmp_path / "test.duckdb"
    df = oi_hist_to_polars([], "BTCUSDT")
    with open_db(db_path) as db:
        assert upsert_oi_history(db, df) == 0


def test_upsert_oi_history_rejects_missing_columns(tmp_path: Path) -> None:
    from pa_assistant.storage import upsert_oi_history

    db_path = tmp_path / "test.duckdb"
    bad = pl.DataFrame({"timestamp": [datetime(2020, 1, 1)]})
    with open_db(db_path) as db, pytest.raises(ValueError, match="missing columns"):
        upsert_oi_history(db, bad)
