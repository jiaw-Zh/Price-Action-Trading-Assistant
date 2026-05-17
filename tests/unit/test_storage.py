"""Tests for :mod:`pa_assistant.storage`."""

from __future__ import annotations

from pathlib import Path

from pa_assistant.storage import CURRENT_SCHEMA_VERSION, Database, open_db
from pa_assistant.storage.schema import TABLE_NAMES


def test_init_schema_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "pa.duckdb"
    with open_db(db_path) as db:
        tables = set(db.list_tables())
        assert set(TABLE_NAMES).issubset(tables)
        assert db.schema_version() == CURRENT_SCHEMA_VERSION


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "pa.duckdb"
    db = Database(db_path)
    db.connect()
    db.init_schema()
    db.init_schema()
    db.init_schema()

    assert db.schema_version() == CURRENT_SCHEMA_VERSION
    conn = db.connect()
    row = conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = ?;",
        [CURRENT_SCHEMA_VERSION],
    ).fetchone()
    assert row is not None
    assert row[0] == 1
    db.close()


def test_kline_1m_insert_and_read(tmp_path: Path) -> None:
    db_path = tmp_path / "pa.duckdb"
    with open_db(db_path) as db:
        conn = db.connect()
        conn.execute(
            """
            INSERT INTO kline_1m (
                open_time, close_time, symbol,
                open, high, low, close,
                volume, quote_volume, trade_count,
                taker_buy_base, taker_buy_quote, is_closed
            )
            VALUES (
                '2025-01-01 00:00:00', '2025-01-01 00:00:59', 'BTCUSDT',
                100.0, 110.0, 95.0, 105.0,
                1000.0, 100500.0, 42,
                600.0, 60300.0, true
            );
            """
        )
        rows = conn.execute("SELECT symbol, close, taker_buy_base FROM kline_1m;").fetchall()
        assert rows == [("BTCUSDT", 105.0, 600.0)]


def test_primary_key_prevents_duplicate_kline(tmp_path: Path) -> None:
    import duckdb

    db_path = tmp_path / "pa.duckdb"
    with open_db(db_path) as db:
        conn = db.connect()
        insert = """
            INSERT INTO kline_1m (
                open_time, close_time, symbol,
                open, high, low, close,
                volume, quote_volume, trade_count,
                taker_buy_base, taker_buy_quote, is_closed
            ) VALUES (
                '2025-01-01 00:00:00', '2025-01-01 00:00:59', 'BTCUSDT',
                1, 1, 1, 1, 1, 1, 1, 1, 1, true
            );
        """
        conn.execute(insert)

        try:
            conn.execute(insert)
        except duckdb.ConstraintException:
            return
        raise AssertionError("expected duplicate-PK insert to raise ConstraintException")


def test_database_is_reopenable(tmp_path: Path) -> None:
    """Closing and reopening the same file preserves data."""
    db_path = tmp_path / "pa.duckdb"
    with open_db(db_path) as db:
        db.connect().execute(
            """
            INSERT INTO oi_1m (timestamp, symbol, open_interest)
            VALUES ('2025-01-01 00:00:00', 'BTCUSDT', 12345.6);
            """
        )

    with open_db(db_path) as db:
        rows = db.connect().execute("SELECT symbol, open_interest FROM oi_1m;").fetchall()
        assert rows == [("BTCUSDT", 12345.6)]
        # Re-init must not bump version a second time
        assert db.schema_version() == CURRENT_SCHEMA_VERSION
